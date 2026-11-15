import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'amr_navigator'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        (
            'share/ament_index/resource_index/packages',
            ['resource/' + package_name],
        ),
        (os.path.join('share', package_name), ['package.xml']),
        (
            os.path.join('share', package_name, 'launch', 'nav2_bringup'),
            glob('launch/nav2_bringup/*.py'),
        ),
        (
            os.path.join('share', package_name, 'params'),
            glob('params/*.yaml'),
        ),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='amr2',
    maintainer_email='amr2@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
)
