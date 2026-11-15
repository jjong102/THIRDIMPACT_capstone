"""Dual LiDAR virtual scan for AMCL. Sensor extrinsics come only from URDF TF."""
import math
import json
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener, TransformException


def apply_transform(points, tf):
    q=tf.rotation; x,y,z,w=q.x,q.y,q.z,q.w
    rotation=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                       [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                       [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    t=tf.translation
    return np.asarray(points) @ rotation.T + [t.x,t.y,t.z]


def scan_points(scan, max_range):
    r=np.asarray(scan.ranges);a=scan.angle_min+np.arange(len(r))*scan.angle_increment
    valid=np.isfinite(r)&(r>max(.1,scan.range_min))&(r<min(max_range,scan.range_max))
    return np.column_stack((r[valid]*np.cos(a[valid]),r[valid]*np.sin(a[valid]),np.zeros(valid.sum())))


def virtual_ranges(points, bins=720, max_range=6.):
    ranges=np.full(bins,np.inf)
    p=np.asarray(points)
    if not len(p):return ranges
    r=np.linalg.norm(p[:,:2],axis=1)
    valid=np.isfinite(p).all(axis=1)&(r>.1)&(r<max_range)
    p,r=p[valid],r[valid]
    index=np.floor((np.arctan2(p[:,1],p[:,0])+math.pi)/(2*math.pi/bins)).astype(int)%bins
    np.minimum.at(ranges,index,r)
    return ranges


class AmclScan(Node):
    def __init__(self):
        super().__init__('third_impact_amcl_scan')
        defaults=dict(front_scan='/rplidar1/scan_filtered',rear_scan='/rplidar2/scan_filtered',
            target_frame='base_link',fixed_frame='odom',max_pair_skew=.15,max_age=.45,
            max_range=6.,bins=720,footprint_half_length=.35,footprint_half_width=.22)
        self.p={k:self.declare_parameter(k,v).value for k,v in defaults.items()}
        if self.p['bins']<60 or self.p['max_age']<=0 or self.p['max_pair_skew']<0:
            raise ValueError('Invalid scan fusion limits')
        self.tf=Buffer();self.listener=TransformListener(self.tf,self)
        self.scans={};self.consumed={};self.last_stamp=0
        qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.pub=self.create_publisher(LaserScan,'/third_impact/amcl_scan',qos)
        self.diag=self.create_publisher(String,'/third_impact/amcl_scan_status',10)
        for side in ('front','rear'):
            self.create_subscription(LaserScan,self.p[side+'_scan'],lambda m,s=side:self.scans.__setitem__(s,m),qos)
        self.create_timer(.05,self.tick)

    def tick(self):
        status=dict(ready=False)
        try:
            if any(s not in self.scans for s in ('front','rear')):
                status['reason']='waiting_for_both_scans';return
            scans=[self.scans[s] for s in ('front','rear')]
            stamps=[Time.from_msg(m.header.stamp) for m in scans]
            ns=[t.nanoseconds for t in stamps];now=self.get_clock().now().nanoseconds
            ages=[(now-t)*1e-9 for t in ns];skew=abs(ns[0]-ns[1])*1e-9
            status.update(ages=ages,pair_skew=skew,frames=[m.header.frame_id for m in scans])
            if any(a<0 or a>self.p['max_age'] for a in ages):
                status['reason']='stale_or_future_scan';return
            if skew>self.p['max_pair_skew']:
                status['reason']='scan_pair_skew';return
            if any(ns[i]<=self.consumed.get(s,-1) for i,s in enumerate(('front','rear'))):
                status['reason']='waiting_for_new_pair';return
            target=stamps[int(np.argmax(ns))]
            if target.nanoseconds<=self.last_stamp:
                status['reason']='nonmonotonic_stamp';return
            clouds=[]
            for m,stamp in zip(scans,stamps):
                # Full roll/pitch/yaw rotation, including upside-down sensor mounts.
                sensor_tf=self.tf.lookup_transform(self.p['target_frame'],m.header.frame_id,stamp).transform
                points=apply_transform(scan_points(m,self.p['max_range']),sensor_tf)
                own=(abs(points[:,0])<=self.p['footprint_half_length'])&(abs(points[:,1])<=self.p['footprint_half_width'])
                points=points[~own]
                if len(points)<10:
                    status['reason']='insufficient_sensor_returns';return
                # Align the two scan timestamps through odom, not through map/AMCL.
                motion=self.tf.lookup_transform_full(self.p['target_frame'],target,
                    self.p['target_frame'],stamp,self.p['fixed_frame']).transform
                clouds.append(apply_transform(points,motion))
            merged=virtual_ranges(np.vstack(clouds),int(self.p['bins']),self.p['max_range'])
            msg=LaserScan();msg.header.frame_id=self.p['target_frame'];msg.header.stamp=target.to_msg()
            msg.angle_min=-math.pi;msg.angle_increment=2*math.pi/self.p['bins']
            msg.angle_max=msg.angle_min+(self.p['bins']-1)*msg.angle_increment
            msg.range_min=.1;msg.range_max=float(self.p['max_range'])
            msg.scan_time=max(m.scan_time for m in scans);msg.time_increment=0.
            msg.ranges=merged.astype(np.float32).tolist()
            self.pub.publish(msg);self.last_stamp=target.nanoseconds
            self.consumed=dict(zip(('front','rear'),ns))
            status.update(ready=True,reason='published',returns=[len(p) for p in clouds],valid_bins=int(np.isfinite(merged).sum()))
        except TransformException as exc:
            status.update(reason='tf_unavailable',error=str(exc))
        finally:
            self.diag.publish(String(data=json.dumps(status)))


def main():
    rclpy.init();node=AmclScan()
    try:rclpy.spin(node)
    finally:node.destroy_node();rclpy.shutdown()
