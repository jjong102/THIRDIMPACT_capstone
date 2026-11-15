// Instrumented equivalent of the Nav2 Humble OscillationCritic policy.
// The policy is kept separate from diagnostics; parity tested against installed Nav2.
#include <array>
#include <cmath>
#include <sstream>
#include <iomanip>
#include <dwb_core/trajectory_critic.hpp>
#include <dwb_core/exceptions.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <std_msgs/msg/string.hpp>

namespace third_impact {
class LoggedOscillationCritic : public dwb_core::TrajectoryCritic {
 struct Trend {
  int sign=0; bool positive=false, negative=false;
  bool update(double v) {
   int next=(v>0)-(v<0);
   bool changed=next!=0 && sign!=0 && next!=sign;
   if(changed) { if(next>0) positive=true; else negative=true; }
   if(next) sign=next;
   return changed;
  }
  bool rejects(double v) const {return (positive && v<0)||(negative && v>0);}
 };
 std::array<Trend,3> trend_{}, at_check_{};
 geometry_msgs::msg::Pose2D pose_{}, reference_{};
 nav_2d_msgs::msg::Twist2D measured_{};
 rclcpp::Clock::SharedPtr clock_;
 rclcpp::Time reference_time_{0,0,RCL_ROS_TIME};
 double distance_=0.05, angle_=0.2, seconds_=-1., threshold_=0.05;
 bool logging_=false;
 uint64_t sequence_=0; size_t rejected_=0, candidates_=0;
 rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::String>::SharedPtr pub_;
 template<class T> T parameter(const std::string & key,T fallback) {
  auto node=node_.lock(); auto full=dwb_plugin_name_+"."+name_+"."+key;
  if(!node->has_parameter(full)) node->declare_parameter(full,rclcpp::ParameterValue(fallback));
  return node->get_parameter(full).get_value<T>();
 }
 static void poseJson(std::ostream &o,const geometry_msgs::msg::Pose2D &p) {o<<"["<<p.x<<","<<p.y<<","<<p.theta<<"]";}
 static void flagsJson(std::ostream &o,const std::array<Trend,3> &flags) {
  o<<"[";
  for(size_t i=0;i<3;++i) {if(i)o<<",";o<<"{\"sign\":"<<flags[i].sign<<",\"positive_only\":"<<flags[i].positive<<",\"negative_only\":"<<flags[i].negative<<"}";}
  o<<"]";
 }
 void record(const char *reason,const std::array<Trend,3> &before,
             const nav_2d_msgs::msg::Twist2D &command,bool flipped,
             double distance,double angular,double elapsed,bool d,bool a,bool t) {
  if(!logging_ || !pub_) return;
  std::ostringstream o;o<<std::setprecision(17)<<std::boolalpha;
  o<<"{\"sequence\":"<<++sequence_<<",\"time\":"<<clock_->now().seconds()
   <<",\"controller\":\""<<dwb_plugin_name_<<"\",\"frame\":\""<<(costmap_ros_?costmap_ros_->getGlobalFrameID():"test")
   <<"\",\"reset_reason\":\""<<reason<<"\",\"sign_flipped\":"<<flipped<<",\"pose\":";
  poseJson(o,pose_);o<<",\"reference_pose\":";poseJson(o,reference_);
  o<<",\"reference_time\":"<<reference_time_.seconds()<<",\"distance\":"<<distance
   <<",\"angular_difference_raw\":"<<angular<<",\"elapsed\":"<<elapsed
   <<",\"distance_threshold\":"<<distance_<<",\"angle_threshold\":"<<angle_
   <<",\"time_threshold\":"<<seconds_<<",\"x_only_threshold\":"<<threshold_
   <<",\"distance_ready\":"<<d<<",\"angle_ready\":"<<a<<",\"time_ready\":"<<t
   <<",\"selected_command\":["<<command.x<<","<<command.y<<","<<command.theta<<"]"
   <<",\"measured_velocity\":["<<measured_.x<<","<<measured_.y<<","<<measured_.theta<<"]"
   <<",\"candidate_count\":"<<candidates_<<",\"rejected_count\":"<<rejected_<<",\"flags_before\":";
  flagsJson(o,before);o<<",\"flags_at_check\":";flagsJson(o,at_check_);o<<",\"flags_after\":";flagsJson(o,trend_);o<<"}";
  std_msgs::msg::String msg;msg.data=o.str();pub_->publish(msg);
 }
public:
 void onInit() override {
  auto node=node_.lock();clock_=node->get_clock();reference_time_=clock_->now();
  distance_=parameter("oscillation_reset_dist",.05);angle_=parameter("oscillation_reset_angle",.2);
  seconds_=parameter("oscillation_reset_time",-1.);threshold_=parameter("x_only_threshold",.05);
  logging_=parameter("debug_logging",false);
  if(logging_) {pub_=node->create_publisher<std_msgs::msg::String>("/third_impact/oscillation",rclcpp::QoS(100).reliable());pub_->on_activate();}
  reset();
 }
 void reset() override {
  auto before=trend_;at_check_=trend_;trend_={};
  if(clock_) record("controller_reset",before,nav_2d_msgs::msg::Twist2D(),false,0.,0.,0.,false,false,false);
 }
 bool prepare(const geometry_msgs::msg::Pose2D &pose,const nav_2d_msgs::msg::Twist2D &vel,
              const geometry_msgs::msg::Pose2D &,const nav_2d_msgs::msg::Path2D &) override {
  pose_=pose;measured_=vel;rejected_=candidates_=0;return true;
 }
 double scoreTrajectory(const dwb_msgs::msg::Trajectory2D &traj) override {
  ++candidates_;
  if(trend_[0].rejects(traj.velocity.x)||trend_[1].rejects(traj.velocity.y)||trend_[2].rejects(traj.velocity.theta)) {
   ++rejected_;throw dwb_core::IllegalTrajectoryException(name_,"Trajectory is oscillating.");
  }
  return 0.;
 }
 void debrief(const nav_2d_msgs::msg::Twist2D &cmd) override {
  auto before=trend_;
  bool changed=trend_[0].update(cmd.x);
  if(threshold_<0 || std::abs(cmd.x)<=threshold_) {
   changed=trend_[1].update(cmd.y)||changed;
   changed=trend_[2].update(cmd.theta)||changed;
  }
  if(changed) {reference_=pose_;reference_time_=clock_->now();}
  double dx=pose_.x-reference_.x,dy=pose_.y-reference_.y,da=pose_.theta-reference_.theta;
  double elapsed=(clock_->now()-reference_time_).seconds();
  bool d=distance_>=0 && dx*dx+dy*dy>distance_*distance_;
  bool a=angle_>=0 && std::abs(da)>angle_;
  bool t=seconds_>=0 && elapsed>seconds_;
  at_check_=trend_;
  bool active=false;for(const auto &f:trend_)active|=f.positive||f.negative;
  const char *reason="none";
  if(active && (d||a||t)) {reason=d?"distance":a?"angle":"time";trend_={};}
  record(reason,before,cmd,changed,std::hypot(dx,dy),da,elapsed,d,a,t);
 }
};
}
PLUGINLIB_EXPORT_CLASS(third_impact::LoggedOscillationCritic,dwb_core::TrajectoryCritic)
