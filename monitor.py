#!/usr/bin/env python

import functools
import os
import subprocess

import rosnode
import rospy

import psutil

try:
  from xmlrpc.client import ServerProxy
except ImportError:
  from xmlrpclib import ServerProxy

from std_msgs.msg import Float32, UInt64


def ns_join(*names):
  return functools.reduce(rospy.names.ns_join, names, "")

class Node:
  def __init__(self, name, pid):
    self.name = name
    self.proc = psutil.Process(pid)
    self.cpu_publisher = rospy.Publisher(ns_join("~", name[1:], "cpu"), Float32, queue_size=20)
    self.mem_publisher = rospy.Publisher(ns_join("~", name[1:], "mem"), UInt64, queue_size=20)

  def publish(self):
    self.cpu_publisher.publish(Float32(self.proc.cpu_percent()))
    self.mem_publisher.publish(UInt64(self.proc.memory_info().rss))

  def alive(self):
    return self.proc.is_running()

if __name__ == "__main__":
  rospy.init_node("cpu_monitor")
  master = rospy.get_master()

  poll_period = rospy.get_param('~poll_period', 1.0)

  try_until_ignore = rospy.get_param('~try_until_ignore', 10)

  this_ip = os.environ.get("ROS_IP")

  node_map = {}
  node_tries = {}
  ignored_nodes = set()

  cpu_publish = rospy.Publisher("~total_cpu", Float32, queue_size=20)

  mem_topics = ["available", "used", "free", "active", "inactive", "buffers", "cached", "shared", "slab"]

  vm = psutil.virtual_memory()
  mem_topics = filter(lambda topic: topic in dir(vm), mem_topics)

  mem_publishers = []
  for mem_topic in mem_topics:
    mem_publishers.append(rospy.Publisher("~total_%s_mem" % mem_topic,
                                          UInt64, queue_size=20))

  while not rospy.is_shutdown():
    try:
      node_names = rosnode.get_node_names()
    except ROSNodeIOException as ex:
      rospy.logerr('Rosnode thinks it cannot communicate with master!')
      rospy.sleep(poll_period)
      continue

    for node in node_names:
      if node in node_map or node in ignored_nodes:
        continue

      try:
        node_api = rosnode.get_api_uri(master, node)[2]
      except ROSNodeIOException as ex:
        rospy.logerr('Rosnode thinks it cannot communicate with master!')
        continue
      if not node_api:
        node_tries[node] = node_tries.get(node, 0) + 1
        if node_tries[node] <= try_until_ignore:
          rospy.logerr("[cpu monitor] failed to get api of node %s (%s)" % (node, node_api))
        continue

      ros_ip = node_api[7:] # strip http://
      ros_ip = ros_ip.split(':')[0] # strip :<port>/
      local_node = "localhost" in node_api or \
                   "127.0.0.1" in node_api or \
                   (this_ip is not None and this_ip == ros_ip) or \
                   subprocess.check_output("hostname").decode('utf-8').strip() in node_api
      if not local_node:
        ignored_nodes.add(node)
        rospy.loginfo("[cpu monitor] ignoring node %s with URI %s" % (node, node_api))
        continue

      try:
        resp = ServerProxy(node_api).getPid('/NODEINFO')
      except:
        rospy.logerr("[cpu monitor] failed to get pid of node %s (api is %s)" % (node, node_api))
      else:
        try:
          pid = resp[2]
        except:
          rospy.logerr("[cpu monitor] failed to get pid for node %s from NODEINFO response: %s" % (node, resp))
        else:
          node_map[node] = Node(name=node, pid=pid)
          rospy.loginfo("[cpu monitor] adding new node %s" % node)

    for node_name, node in list(node_map.items()):
      if node.alive():
        node.publish()
      else:
        rospy.logwarn("[cpu monitor] lost node %s" % node_name)
        del node_map[node_name]

    cpu_publish.publish(Float32(psutil.cpu_percent()))

    vm = psutil.virtual_memory()
    for mem_topic, mem_publisher in zip(mem_topics, mem_publishers):
      mem_publisher.publish(UInt64(getattr(vm, mem_topic)))

    rospy.sleep(poll_period)
