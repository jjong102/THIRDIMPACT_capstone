"""Teaching-only interactive rectangle; debug never starts this server."""
import math
from geometry_msgs.msg import Pose
from interactive_markers.interactive_marker_server import InteractiveMarkerServer
from visualization_msgs.msg import InteractiveMarker, InteractiveMarkerControl, Marker


def yaw(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))


def orientation(pose, angle):
    pose.orientation.z = math.sin(angle/2)
    pose.orientation.w = math.cos(angle/2)


class RectangleEditor:
    def __init__(self, node, namespace='third_impact/rectangle', color=(1., .4, .1), size=1.):
        self.server = InteractiveMarkerServer(node, namespace)
        self.color = color
        self.default_size = size
        self.rect = None

    def reset(self, pose, rectangle=None):
        self.rect = list(rectangle) if rectangle else [*pose, self.default_size, self.default_size]
        self.render()

    def render(self):
        self.server.clear()
        x, y, a, width, height = self.rect
        marker = InteractiveMarker()
        marker.header.frame_id = 'map'
        marker.name = 'rectangle'
        marker.description = ''  # Instructions live in the left teaching panel.
        marker.scale = max(width, height, 1.)
        marker.pose.position.x, marker.pose.position.y = x, y
        orientation(marker.pose, a)
        visual = InteractiveMarkerControl()
        visual.always_visible = True
        box = Marker(); box.type = Marker.CUBE
        box.pose.orientation.w = 1.; box.pose.position.z = .025
        box.scale.x, box.scale.y, box.scale.z = width, height, .05
        box.color.r, box.color.g, box.color.b = self.color
        box.color.a = .25
        visual.markers.append(box)
        visual.name = 'move'; visual.interaction_mode = InteractiveMarkerControl.MOVE_PLANE
        visual.orientation.w = math.sqrt(.5); visual.orientation.y = math.sqrt(.5)
        marker.controls.append(visual)
        rotate = InteractiveMarkerControl()
        rotate.name = 'rotate'; rotate.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
        rotate.orientation.w = math.sqrt(.5); rotate.orientation.y = math.sqrt(.5)
        marker.controls.append(rotate)
        self.server.insert(marker, feedback_callback=self.move)
        corner = InteractiveMarker(); corner.header.frame_id = 'map'
        corner.name = 'size'; corner.description = ''; corner.scale = .3
        corner.pose.position.x = x+math.cos(a)*width/2-math.sin(a)*height/2
        corner.pose.position.y = y+math.sin(a)*width/2+math.cos(a)*height/2
        corner.pose.orientation.w = 1.
        control = InteractiveMarkerControl(); control.name = 'size'
        control.orientation.w = math.sqrt(.5); control.orientation.y = math.sqrt(.5)
        control.interaction_mode = InteractiveMarkerControl.MOVE_PLANE; control.always_visible = True
        sphere = Marker(); sphere.type = Marker.SPHERE; sphere.pose.orientation.w = 1.
        sphere.scale.x = sphere.scale.y = sphere.scale.z = .14
        sphere.color.r = sphere.color.g = sphere.color.a = 1.
        control.markers.append(sphere); corner.controls.append(control)
        self.server.insert(corner, feedback_callback=self.resize)
        self.server.applyChanges()

    def move(self, feedback):
        if feedback.event_type == feedback.MOUSE_UP:
            self.rect[:3] = [feedback.pose.position.x, feedback.pose.position.y, yaw(feedback.pose.orientation)]
            self.render()

    def resize(self, feedback):
        if feedback.event_type == feedback.MOUSE_UP:
            dx, dy = feedback.pose.position.x-self.rect[0], feedback.pose.position.y-self.rect[1]
            c, s = math.cos(self.rect[2]), math.sin(self.rect[2])
            self.rect[3:] = [max(.1, 2*abs(c*dx+s*dy)), max(.1, 2*abs(-s*dx+c*dy))]
            self.render()
