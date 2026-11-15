"""Wheel geometry from the same AMR URDF used by robot_state_publisher."""
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def wheel_geometry(urdf_path):
    robot = ET.parse(Path(urdf_path)).getroot()
    radii = [float(robot.find(
        f"link[@name='wheel_{side}_link']/visual/geometry/cylinder").get('radius'))
        for side in ('left', 'right')]
    joints = [robot.find(f"joint[@name='wheel_{side}_joint']")
              for side in ('left', 'right')]
    if joints[0].find('parent').get('link') != joints[1].find('parent').get('link'):
        raise ValueError('Wheel joints must use the same parent frame')
    positions = [list(map(float, joint.find('origin').get('xyz').split())) for joint in joints]
    separation = abs(positions[0][1] - positions[1][1])
    if not all(math.isfinite(v) and v > 0 for v in (*radii, separation)):
        raise ValueError('Wheel dimensions must be finite and positive')
    if not math.isclose(radii[0], radii[1], rel_tol=1e-6):
        raise ValueError('Differential drive currently requires equal wheel radii')
    return 2 * radii[0], separation
