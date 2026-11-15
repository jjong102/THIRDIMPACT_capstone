#include "third_impact/goal_panel.hpp"
#include <QVBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QHeaderView>
#include <QRegularExpression>
#include <QScrollBar>
#include <cmath>
#include <algorithm>
#include <pluginlib/class_list_macros.hpp>
namespace third_impact {
namespace {
QString localizationName(const QString &state) {
 if(state=="READY") return "위치 확인됨";
 if(state=="OCCLUDED_TRACKING") return "일부 가림 · 위치 추적 유지";
 if(state=="OCCLUDED_HOLD") return "가림 관측 부족 · 정지 대기";
 if(state=="TRACKING_HOLD") return "위치 추정 유지 · 주행 정지";
 if(state=="SEARCHING") return "자동 위치 탐색 중";
 if(state=="INITIALIZING") return "AMCL 자동 초기화 중";
 if(state=="VERIFYING") return "위치 후보 검증 중";
 if(state=="AMBIGUOUS") return "위치 후보가 여러 개";
 if(state=="LOCAL_RECHECK") return "정지 후 위치 재검증";
 if(state=="DEGRADED") return "위치 품질 저하 확인 중";
 if(state=="SEARCH_FAILED") return "위치 탐색 재시도";
 if(state=="WAIT_STOP") return "정지 대기";
 if(state=="WAIT_AMCL") return "AMCL 시작 대기";
 if(state=="WAIT_MAP") return "지도 수신 대기";
 if(state=="WAIT_SENSORS") return "LiDAR·odometry 대기";
 return "자동 위치 노드 대기";
}
QString actionResultName(int status) {
 switch(status) {
  case 0: return "확인 불가"; case 1: return "수락됨"; case 2: return "실행 중";
  case 3: return "취소 처리 중"; case 4: return "성공";
  case 5: return "취소됨"; case 6: return "실패"; default: return "알 수 없는 값";
 }
}
QJsonObject legacyResult(const QString &detail) {
 auto match=QRegularExpression("Navigation action finished: purpose=(\\w+), status=(\\d+), cancellation=(True|False)").match(detail);
 if (!match.hasMatch()) return {};
 return {{"purpose",match.captured(1)}, {"status",match.captured(2).toInt()},
         {"cancel_requested",match.captured(3)=="True"}};
}
QString readableDetail(const QString &detail) {
 auto old=legacyResult(detail);
 if (!old.isEmpty()) return QString("%1 이동 결과: %2")
   .arg(old["purpose"].toString()=="patrol" ? "정찰" : "복귀")
   .arg(actionResultName(old["status"].toInt()));
 if (detail=="Navigation rejected; retrying") return "Nav2가 이동 요청을 거절했습니다. 재시도 중입니다.";
 if (detail=="Navigation request pending") return "Nav2에 이동을 요청했습니다. 수락 응답을 기다립니다.";
 if (detail=="Waiting for Nav2 action server") return "Nav2 이동 서버가 준비되기를 기다립니다.";
 if (detail=="Navigation accepted: patrol") return "Nav2가 정찰 목적지 이동 요청을 수락했습니다.";
 if (detail=="Navigation accepted: return") return "Nav2가 저장 목표로 복귀하는 요청을 수락했습니다.";
 if (detail=="No valid patrol candidate; retrying") return "이동 가능한 정찰 목적지를 다시 찾고 있습니다.";
 if (detail=="Paused: manual control or localization/sensor readiness") return "수동 조작 또는 위치·센서 준비 상태 때문에 자율주행을 대기합니다.";
 if (detail=="Alignment paused: ambiguous lidar match; reobserving") return "LiDAR 정합이 불확실하여 정지 후 다시 관측합니다.";
 if (detail=="Alignment blocked: waiting for clearance; retry remains active") return "정렬 경로의 장애물이 해소되기를 기다립니다.";
 if (detail=="Manual control: mission paused") return "수동 조작 모드입니다. 자율 임무는 일시 중지됩니다.";
 if (detail=="Automatic control: resume mission") return "자동 모드로 전환하여 임무를 재개합니다.";
 return detail;
}
}
StatusPanel::StatusPanel(QWidget *parent): Panel(parent) {
 node_=std::make_shared<rclcpp::Node>("third_impact_status_panel");
 executor_.add_node(node_);
 auto *layout=new QVBoxLayout;
 state_=new QLabel("연결 대기 중"); state_->setObjectName("mission_state");
 state_->setStyleSheet("font-size: 20px; font-weight: bold; color: #e0a020;");
 state_->setWordWrap(true); state_->setTextFormat(Qt::PlainText);
 detail_=new QLabel("Jetson의 상태 정보를 기다리는 중입니다.");
 detail_->setObjectName("mission_detail");
 detail_->setWordWrap(true); detail_->setTextFormat(Qt::PlainText);
 table_=new QTableWidget(0,3); table_->setObjectName("mission_table");
 table_->setHorizontalHeaderLabels({"항목", "값", "간단한 설명"});
 table_->verticalHeader()->hide();
 table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
 table_->setSelectionMode(QAbstractItemView::NoSelection);
 table_->setWordWrap(true);
 table_->horizontalHeader()->setSectionResizeMode(0,QHeaderView::ResizeToContents);
 table_->horizontalHeader()->setSectionResizeMode(1,QHeaderView::ResizeToContents);
 table_->horizontalHeader()->setSectionResizeMode(2,QHeaderView::Stretch);
 table_->setMinimumHeight(230);
 layout->addWidget(state_); layout->addWidget(detail_); layout->addWidget(table_); setLayout(layout);
 setMinimumHeight(310);
 status_=node_->create_subscription<std_msgs::msg::String>("/third_impact/status",1,
 [this](std_msgs::msg::String::ConstSharedPtr msg){
  QJsonParseError error;
  auto document=QJsonDocument::fromJson(QByteArray::fromStdString(msg->data), &error);
  if (error.error!=QJsonParseError::NoError || !document.isObject()) return;
  auto obj=document.object();
  received_.start();
  const auto state=obj["state"].toString();
  const auto localization=obj["localization"].toObject();
  QString title, explanation, color="#e0a020";
  if (obj["mode"].toString()=="teach") {
   title=state=="CAPTURE" ? "목표 저장 중" : "목표 설정 모드";
   explanation=state=="CAPTURE" ? "로봇을 정지한 채 기다려 주세요." : "수동으로 주차한 뒤 목표, 한계 구간, 정찰 제한 구간을 저장하세요.";
  } else if (obj["manual"].isNull() || obj["manual"].isUndefined()) {
   title="준비 대기"; explanation="수동·자동 모드 정보를 기다리고 있습니다.";
  } else if (obj["manual"].toBool()) {
   title="수동 조작 중"; explanation="자율 임무 일시 중지 · 자동 모드 전환 시 이전 임무를 재개합니다.";
  } else if (state=="HOLD") {
   title="도착 완료 · 대기 중"; color="#36b85a";
   explanation="도착 알림 발행 완료 · go_around 명령을 기다립니다.";
  } else if (obj.contains("ready") && !obj["ready"].toBool()) {
   title="위치·센서 준비 대기"; explanation="현재 위치 TF, AMCL, LiDAR, odometry 연결을 확인하세요.";
   if(localization["fresh"].toBool() && !localization["ready"].toBool()) {
    title=localizationName(localization["state"].toString());
    explanation=localization["reason"].toString();
   }
  } else if (!obj["has_goal"].toBool() || !obj["has_rectangle"].toBool() || !obj["has_boundary"].toBool() || state=="WAIT") {
   title="목표·구역 설정 대기"; explanation="저장된 목표, 한계 구간, 정찰 제한 구간이 필요합니다.";
  } else if (state=="PATROL_APPROACH") {
   title="정찰 목표 위치로 근접 이동"; explanation="최종 방향과 무관하게 목표 위치 5cm 이내로 접근합니다.";
  } else if (state=="PATROL_SEARCH") {
   title="정찰 목표·경로 탐색 중"; explanation="현재 위치에서 도달 가능한 목표와 실제 경로를 확인합니다. 상세 사유를 확인하세요.";
  } else if (state=="PATROL_BLOCKED") {
   title="정찰 경로 차단 · 대기"; explanation="정찰 제어가 대기 중입니다. 상세 안내의 정체·시간 초과·장애물 사유를 확인하세요.";
  } else if (state=="PATROL_RECOVERY") {
   title="정찰 방향 정렬·경로 재시도"; explanation="같은 목표를 유지하며 안전한 방향과 우회 경로를 확인합니다.";
  } else if (state=="RETURN_WAIT") {
   title="복귀 재접근 대기"; explanation="재접근 한도 또는 안전한 진입 경로를 확인하세요. 도착 완료 상태가 아닙니다.";
  } else if (state=="ALIGN") {
   title="목표 위치 정밀 정렬 중"; color="#42a5f5";
   const auto phase=obj["alignment"].toObject()["phase"].toString();
   if(phase.startsWith("COARSE_") && phase!="COARSE_DONE") title="목표 방향 먼저 정렬 중";
   if(phase=="REVERIFY" || phase=="FINE_VERIFY") title="정밀 정합 재검증 중";
   if(phase.startsWith("MICRO_")) title="목표 근처 짧은 위치 보정 중";
   explanation="위치·방향 보정 후 정지 유지 조건을 확인합니다.";
  } else if (state=="RETURN_ROTATE") {
   title="진입 전 방향 정렬 중"; explanation="회전 가능한 위치에서 저장 목표의 방향으로 맞춥니다.";
  } else if (state=="RETURN_REVERSE") {
   title="후진 복귀 중"; explanation="장애물이 있으면 우회 없이 정지하고 해소를 기다립니다.";
  } else if (state=="RETURN_FORWARD") {
   title="전진 복귀 중"; explanation="장애물 앞에서 정지하고 5초 후에도 막혀 있으면 우회합니다.";
  } else if (state=="RETURN") {
   title="목표로 복귀 중"; color="#42a5f5";
   explanation="저장된 goal로 이동하는 임무입니다.";
  } else if (state=="PATROL") {
   title="정찰 중"; color="#36b85a";
   explanation="랜덤 목적지를 선정해 이동하는 임무입니다.";
  } else { title="상태 확인 중"; explanation=state; }
  if(obj["ready"].toBool() && !obj["manual"].toBool() &&
     obj["intent"].toString()=="return" && obj["return"].toObject()["blocked_seconds"].toDouble()>0.) {
   title="장애물 해소 대기 중"; color="#e89b00";
   explanation=obj["return"].toObject()["waiting"].toString();
  }
  state_->setText(title);
  state_->setStyleSheet(QString("font-size: 20px; font-weight: bold; color: %1;").arg(color));
  auto detail=readableDetail(obj["detail"].toString());
  if (obj["detail"].toString()=="Navigation rejected; retrying" &&
      !obj["manual"].toBool() && (state=="PATROL" || state=="RETURN") &&
      (!obj.contains("ready") || obj["ready"].toBool())) {
   title=state=="PATROL" ? "정찰 요청 재시도 중" : "복귀 요청 재시도 중";
   if(obj["ready"].toBool() && !obj["manual"].toBool() &&
     obj["intent"].toString()=="return" && obj["return"].toObject()["blocked_seconds"].toDouble()>0.) {
   title="장애물 해소 대기 중"; color="#e89b00";
   explanation=obj["return"].toObject()["waiting"].toString();
  }
  state_->setText(title);
   state_->setStyleSheet("font-size: 20px; font-weight: bold; color: #e0a020;");
  }
  const int scroll=table_->verticalScrollBar()->value();
  table_->setRowCount(0);
  auto row=[this](const QString &name, const QString &value, const QString &meaning) {
   const int i=table_->rowCount(); table_->insertRow(i);
   const QString cells[]={name,value,meaning};
   for(int c=0;c<3;++c) {
    auto *item=new QTableWidgetItem(cells[c]); item->setToolTip(cells[c]); table_->setItem(i,c,item);
   }
  };
  row("임무", obj["mode"].toString()=="teach" ? "목표 설정" : obj["intent"].toString()=="return" ? "목표 복귀" : obj["intent"].toString()=="hold" ? "도착 대기" : "정찰", explanation);
  row("조작 모드",obj["manual"].isBool() ? (obj["manual"].toBool() ? "수동" : "자동") : "확인 중", "수동에서는 자율 임무를 일시 중지합니다.");
  row("위치·센서",obj.contains("ready") ? (obj["ready"].toBool() ? "준비됨" : "대기") : "정보 없음", "TF, AMCL, LiDAR, odometry 준비 여부입니다.");
  row("초기 위치",localization["fresh"].toBool() ? localizationName(localization["state"].toString()) : "노드 수신 대기",
      localization["fresh"].toBool() ? localization["reason"].toString() : "Jetson의 자동 위치 탐색 노드 정보를 기다립니다.");
  const auto quality=localization["quality"].toObject();
  if(quality.contains("reason"))
   row("위치 검증",quality["valid"].toBool() ? "통과" : "거부",quality["reason"].toString());
  const auto sensors=quality["sensors"].toObject();
  for(const auto &key : {QString("front"),QString("rear")}) {
   const auto sensor=sensors[key].toObject();
   if(!sensor.isEmpty())
    row(key=="front" ? "전방 벽 일치" : "후방 벽 일치",
        QString("%1 %").arg(sensor["overlap"].toDouble()*100.,0,'f',1),
        sensor.contains("occluded")
          ? QString("벽 관측 %1 · 가림 후보 %2 · 불일치 %3").arg(sensor["matched"].toInt()).arg(sensor["occluded"].toInt()).arg(sensor["contradictory"].toInt())
          : QString("광선 자유 공간 %1% · %2").arg(sensor["rays_free_fraction"].toDouble()*100.,0,'f',1).arg(sensor["reason"].toString()));
  }

  if(quality.contains("overlap"))
   row("지도 일치율",QString("%1 %").arg(quality["overlap"].toDouble()*100.,0,'f',0),"LiDAR 끝점이 지도 벽 경계 12cm 이내에 있는 비율입니다. 장애물 내부는 일치로 간주하지 않습니다.");
  const auto navigation=obj["navigation"].toObject();
  const auto phase=navigation["phase"].toString();
  row("이동 요청",phase=="executing" ? "수락됨" : phase=="requesting" ? "응답 대기" : phase=="cancel_requested" ? "취소 요청" : phase=="idle" ? "없음" : "정보 없음", "Nav2에 보낸 현재 이동 요청 상태입니다.");
  auto last=navigation["last_result"].toObject();
  if(last.isEmpty()) last=legacyResult(obj["detail"].toString());
  const int code=last["status"].toInt(-1);
  row("직전 결과",code<0 ? "없음" : QString("%1 (%2)").arg(actionResultName(code)).arg(code),
      "4=성공, 5=취소, 6=실패. Nav2 이동 결과이며 정밀 복귀 도착 알림과는 별도입니다.");
  row("직전 이동",last.isEmpty() ? "없음" : last["purpose"].toString()=="patrol" ? "정찰" : "목표 복귀", "직전 결과가 어느 이동에 대한 것인지 나타냅니다.");
  row("취소 요청",last.isEmpty() ? "없음" : last["cancel_requested"].toBool() ? "요청함" : "요청 안 함",
      last["patrol_arrived"].toBool() ? "정찰 목적지 도착으로 다음 이동을 준비합니다. 취소 결과도 정상 전환일 수 있습니다." : "직전 이동에 취소를 요청했는지입니다. False는 요청하지 않았다는 뜻입니다.");
  const auto distance=navigation["distance_m"];
  row("남은 거리",distance.isDouble() ? QString("%1 cm").arg(distance.toDouble()*100.,0,'f',1) : "—", "현재 이동 목적지까지의 직선거리입니다.");
  row("정찰 도착",QString("%1 cm 이내").arg(navigation["patrol_tolerance_m"].toDouble(.05)*100.,0,'f',0), "방향 무관. 정지 유지 없이 이전 이동 종료 확인 후 다음 목적지를 선택합니다.");
  row("정찰 완료",navigation.contains("patrol_visits") ? QString::number(navigation["patrol_visits"].toInt())+"회" : "—", "이번 실행에서 도착한 정찰 목적지 수입니다.");
  const auto pose=obj["match"].toObject()["pose"].toArray();
  if (state=="ALIGN" && pose.size()==3) {
   row("정렬 오차",QString("%1 mm / %2°")
     .arg(1000.*std::hypot(pose[0].toDouble(),pose[1].toDouble()),0,'f',1)
     .arg(pose[2].toDouble()*180./std::acos(-1.),0,'f',2), "저장 goal 대비 최근 LiDAR 정합의 위치·방향 추정 오차입니다.");
  }
  const auto alignment=obj["alignment"].toObject();
  if(state=="ALIGN" && !alignment.isEmpty()) {
   const auto phase=alignment["phase"].toString();
   const auto stage=phase=="POSITION" ? "위치 우선 보정" : phase=="COARSE_WAIT" ? "회전 공간·진행 확인 대기" :
     phase.startsWith("COARSE_") ? "큰 방향 정렬" :
     phase.startsWith("MICRO_SETTLE") ? "짧은 보정 후 정지 확인" :
     phase=="MICRO_OUT" ? "짧은 보정 진입점으로 이동" :
     phase=="MICRO_IN" ? "보정된 목표 위치로 접근" :
     phase=="FINE_ALIGN" ? "정밀 정렬" : "새 스캔으로 재검증";
   row("정렬 단계",stage,"위치 보정 → 최종 방향 정렬 → 정밀 검증");
   if(alignment.contains("longitudinal_m"))
    row("종·횡 위치 오차",QString("%1 / %2 cm").arg(alignment["longitudinal_m"].toDouble()*100.,0,'f',1).arg(alignment["lateral_m"].toDouble()*100.,0,'f',1),"저장 목표 방향 기준 종방향 / 횡방향 오차");
   if(alignment.contains("heading_error_deg"))
    row("방향 오차",QString::number(alignment["heading_error_deg"].toDouble(),'f',1)+"°","현재 정렬 단계의 방향 오차");
   if(alignment.contains("updated_at"))
    row("정렬 정보 갱신",QString::number(std::max(0.,obj["time"].toDouble()-alignment["updated_at"].toDouble()),'f',1)+"초 전","지난 결과를 현재 검사로 오인하지 않도록 갱신 시각을 표시합니다.");
   if(alignment.contains("correction_m"))
    row("위치 보정 검사",QString("%1 / %2 cm").arg(alignment["correction_m"].toDouble()*100.,0,'f',1).arg(alignment["correction_limit_m"].toDouble()*100.,0,'f',1),"AMCL/TF 대비 LiDAR 정합 보정량 / 허용 한계");
   if(!alignment["rejection"].toString().isEmpty())
    row("정렬 거부 사유","상세",alignment["rejection"].toString());
  }
  auto ret=obj["return"].toObject();
  if(obj["intent"].toString()=="return") {
   row("장애물 대기", QString::number(ret["blocked_seconds"].toDouble(), 'f', 1)+"초", ret["waiting"].toString());
  }
  row("최근 안내", "상세", detail.isEmpty() ? "없음" : detail);
  table_->resizeRowsToContents();
  table_->verticalScrollBar()->setValue(scroll);
  detail_->setText(explanation);
 });
 timer_=new QTimer(this);
 connect(timer_, &QTimer::timeout, this, [this](){
  if (!rclcpp::ok(node_->get_node_base_interface()->get_context())) {
   timer_->stop(); state_->setText("ROS 연결 종료"); table_->setRowCount(0); return;
  }
  try { executor_.spin_some(); }
  catch (const rclcpp::exceptions::RCLError &) {
   timer_->stop(); state_->setText("ROS 연결 오류"); table_->setRowCount(0); return;
  }
  if (received_.isValid() && received_.elapsed()>2000) {
   state_->setText("상태 수신 끊김");
   state_->setStyleSheet("font-size: 20px; font-weight: bold; color: #ef5350;");
   detail_->setText("2초 이상 상태를 받지 못했습니다. Jetson 실행 상태와 네트워크를 확인하세요.");
   table_->setRowCount(0);
  }
 });
 timer_->start(50);
}
StatusPanel::~StatusPanel() { timer_->stop(); }
GoalPanel::GoalPanel(QWidget *parent): Panel(parent) {
 node_ = std::make_shared<rclcpp::Node>("third_impact_goal_panel");
 executor_.add_node(node_);
 goal_=node_->create_publisher<std_msgs::msg::Empty>("/save_goal",10);
 boundary_=node_->create_publisher<std_msgs::msg::Empty>("/third_impact/save_boundary",10);
 region_=node_->create_publisher<std_msgs::msg::Empty>("/third_impact/save_region",10);
 auto *layout=new QVBoxLayout;
 label_=new QLabel("Jetson의 목표 설정 노드 연결 대기 중..."); label_->setWordWrap(true);
 label_->setTextFormat(Qt::PlainText);
 setMinimumHeight(180);
 save_=new QPushButton("Save Goal — 현재 위치·방향 저장");
 boundary_save_=new QPushButton("Save Boundary — 한계 구간 저장 (파랑)");
 apply_=new QPushButton("Save Region — 정찰 제한 구간 저장 (주황)");
 save_->setEnabled(false); apply_->setEnabled(false); boundary_save_->setEnabled(false);
 layout->addWidget(label_); layout->addWidget(save_); layout->addWidget(boundary_save_); layout->addWidget(apply_); setLayout(layout);
 connect(save_, &QPushButton::clicked, this, [this](){
  if (rclcpp::ok(node_->get_node_base_interface()->get_context())) goal_->publish(std_msgs::msg::Empty());
 });
 connect(boundary_save_, &QPushButton::clicked, this, [this](){
  if (rclcpp::ok(node_->get_node_base_interface()->get_context())) boundary_->publish(std_msgs::msg::Empty());
 });
 connect(apply_, &QPushButton::clicked, this, [this](){
  if (rclcpp::ok(node_->get_node_base_interface()->get_context())) region_->publish(std_msgs::msg::Empty());
 });
 status_=node_->create_subscription<std_msgs::msg::String>("/third_impact/status",1,
 [this](std_msgs::msg::String::ConstSharedPtr msg){
  auto obj=QJsonDocument::fromJson(QByteArray::fromStdString(msg->data)).object();
  bool teaching=obj["mode"].toString()=="teach";
  save_->setEnabled(teaching && obj["state"].toString()!="CAPTURE");
  apply_->setEnabled(teaching && obj["has_goal"].toBool() && obj["state"].toString()!="CAPTURE");
  boundary_save_->setEnabled(teaching && obj["has_goal"].toBool() && obj["state"].toString()!="CAPTURE");
  const bool boundary=obj["has_boundary"].toBool();
  const bool captured=obj["has_goal"].toBool();
  const bool region=obj["has_rectangle"].toBool();
  QString instruction;
  if (!teaching) instruction="임무 실행 중입니다. 목표 변경은 make_goal_launch에서 가능합니다.";
  else if (obj["state"].toString()=="CAPTURE") instruction="목표 관측 수집 중: 로봇을 움직이지 마세요.";
  else if (!captured) instruction="로봇을 주차하고 정지한 뒤 Save Goal을 누르세요.";
  else if (!boundary || !region) instruction="Interact: 면 이동 · 고리 회전 · 모서리 크기 조절. 파랑=이동 한계, 주황=정찰 제외. 각각 저장하세요.";
  else instruction="목표와 두 구간 저장 완료. 목표 설정 launch 종료 후 임무를 시작하세요.";
  label_->setText(QString("목표: %1 | 한계: %2 | 정찰 제한: %3\n%4\n%5")
    .arg(captured ? "저장됨" : "미저장").arg(boundary ? "저장됨" : "미저장").arg(region ? "저장됨" : "미저장")
    .arg(instruction).arg(readableDetail(obj["detail"].toString())));
 });
 timer_=new QTimer(this); connect(timer_, &QTimer::timeout,this,[this](){
  if (!rclcpp::ok(node_->get_node_base_interface()->get_context())) {
   timer_->stop(); save_->setEnabled(false); apply_->setEnabled(false); boundary_save_->setEnabled(false); return;
  }
  // Keep one executor: spin_some(node_) constructed a fresh executor/guard
  // condition on every Qt tick, including ticks after ROS SIGINT shutdown.
  try { executor_.spin_some(); }
  catch (const rclcpp::exceptions::RCLError &error) {
   timer_->stop(); save_->setEnabled(false); apply_->setEnabled(false); boundary_save_->setEnabled(false);
   if (rclcpp::ok(node_->get_node_base_interface()->get_context()))
    label_->setText(QString("ROS 연결 오류: %1").arg(error.what()));
  }
 }); timer_->start(50);
}
GoalPanel::~GoalPanel() { timer_->stop(); }
}
PLUGINLIB_EXPORT_CLASS(third_impact::GoalPanel, rviz_common::Panel)
PLUGINLIB_EXPORT_CLASS(third_impact::StatusPanel, rviz_common::Panel)
