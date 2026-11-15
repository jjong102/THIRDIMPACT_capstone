#pragma once
#include <rviz_common/panel.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/string.hpp>
#include <QLabel>
#include <QPushButton>
#include <QTimer>
#include <QElapsedTimer>
#include <QTableWidget>
namespace third_impact {
class StatusPanel : public rviz_common::Panel {
 Q_OBJECT
 public: explicit StatusPanel(QWidget *parent=nullptr);
 ~StatusPanel() override;
 private:
 rclcpp::Node::SharedPtr node_;
 rclcpp::executors::SingleThreadedExecutor executor_;
 rclcpp::Subscription<std_msgs::msg::String>::SharedPtr status_;
 QLabel *state_, *detail_;
 QTableWidget *table_;
 QTimer *timer_;
 QElapsedTimer received_;
};
class GoalPanel : public rviz_common::Panel {
 Q_OBJECT
 public: explicit GoalPanel(QWidget *parent=nullptr);
 ~GoalPanel() override;
 private:
 rclcpp::Node::SharedPtr node_;
 rclcpp::executors::SingleThreadedExecutor executor_;
 rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr goal_, region_, boundary_;
 rclcpp::Subscription<std_msgs::msg::String>::SharedPtr status_;
 QLabel *label_; QPushButton *save_, *apply_, *boundary_save_; QTimer *timer_;
};
}
