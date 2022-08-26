#!/usr/bin/env python

"""
    create_load_balancer.py:
    Lambda handler that is invoked by an AWS Step Functions
    State Machine as part of an ALB Scale-Out operation.
    This handler creates a new ALB, listener and target group
    and adds the ECS tasks as targets of the target group.
"""

import datetime
import json
import logging
import os
import random
import string
import traceback

import boto3
import botocore.exceptions

from ..services.constants_service import ConstantsService
from ..services.fleet_service import FleetService
from ..services.notifier_service import NotifierService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
elbv2_client = boto3.client('elbv2')
ssm_client = boto3.client('ssm')
ecs_client = boto3.client("ecs")

# get env vars
ALB_VPC_ID = os.environ['ALB_VPC_ID']
ALB_SUBNET_IDS = os.environ['ALB_SUBNET_IDS'].split(",")
ALB_SECURITY_GROUPS = os.environ['ALB_SECURITY_GROUPS'].split(",")
ALB_TAG_KEY = os.environ['ALB_TAG_KEY']
ALB_TAG_VALUE = os.environ['ALB_TAG_VALUE']
ECS_CLUSTER_ARN = os.environ['ECS_CLUSTER_ARN']
FLEET_TAG_KEY = os.environ['FLEET_TAG_KEY']
FLEET_TAG_VALUE = os.environ['FLEET_TAG_VALUE']
SUSPEND_ADD_ALB_PARAM_NAME = os.environ['SUSPEND_ADD_ALB_PARAM_NAME']

# static vars
OPERATOR = "CREATE_LOAD_BALANCER"
TARGET_GROUP_PROTOCOL = "HTTP"
TARGET_GROUP_PROTOCOL_VERSION = "HTTP1"
ALB_LISTENER_PORT = 80
TARGET_GROUP_PORT = 80
TARGET_GROUP_HEALTH_CHECK_PORT = "80"
TARGET_GROUP_HEALTH_CHECK_PROTOCOL = "HTTP"
TARGET_GROUP_HEALTH_CHECK_PATH = "/"
TARGET_GROUP_HEALTH_CHECK_INTERVAL = 30
TARGET_GROUP_HEALTH_CHECK_TIMEOUT = 5
TARGET_GROUP_HEALTH_HEALTHY_COUNT = 5
TARGET_GROUP_HEALTH_UNHEALTHY_COUNT = 2
TARGET_GROUP_HEALTH_MATCHER_TYPE = "HttpCode"
TARGET_GROUP_HEALTH_MATCHER_CODE = "200"
TARGET_GROUP_TARGET_TYPE = "ip"
ALB_NO_ROUTE_HTTP_CODE = "418"
ALB_NO_ROUTE_FIXED_RESPONSE = "No router match found."

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()


def get_ecs_task_ips(cluster_arn: str) -> list[str]:
    paginator = ecs_client.get_paginator('list_tasks')
    response_iterator = paginator.paginate(
        cluster=cluster_arn,
        PaginationConfig={
            'PageSize':100
        }
    )

    enis = []
    for each_page in response_iterator:
        for each_task in each_page['taskArns']:
            tasks_detail = ecs_client.describe_tasks(
                cluster=cluster_arn,
                tasks=[each_task]
            )
            for task in tasks_detail.get("tasks", []):
                for attachment in task.get("attachments", []):
                    for detail in attachment.get("details", []):
                        if detail.get("name") == "networkInterfaceId":
                            enis.append(detail.get("value"))

    # now the ips
    ips = []
    for eni in enis:
        eni_resource = boto3.resource("ec2").NetworkInterface(eni)
        ips.append(eni_resource.private_ip_address)

    return ips


def create_target_group(target_group_id: str) -> dict:
    # create the target groups
    response = elbv2_client.create_target_group(
        Name=target_group_id,
        Protocol=TARGET_GROUP_PROTOCOL,
        ProtocolVersion=TARGET_GROUP_PROTOCOL_VERSION,
        Port=ALB_LISTENER_PORT,
        VpcId=ALB_VPC_ID,
        HealthCheckProtocol=TARGET_GROUP_HEALTH_CHECK_PROTOCOL,
        HealthCheckPort=TARGET_GROUP_HEALTH_CHECK_PORT,
        HealthCheckEnabled=True,
        HealthCheckPath=TARGET_GROUP_HEALTH_CHECK_PATH,
        HealthCheckIntervalSeconds=TARGET_GROUP_HEALTH_CHECK_INTERVAL,
        HealthCheckTimeoutSeconds=TARGET_GROUP_HEALTH_CHECK_TIMEOUT,
        HealthyThresholdCount=TARGET_GROUP_HEALTH_HEALTHY_COUNT,
        UnhealthyThresholdCount=TARGET_GROUP_HEALTH_UNHEALTHY_COUNT,
        Matcher={
            TARGET_GROUP_HEALTH_MATCHER_TYPE: TARGET_GROUP_HEALTH_MATCHER_CODE
        },
        TargetType=TARGET_GROUP_TARGET_TYPE,
        Tags=[
            {
                'Key': FLEET_TAG_KEY,
                'Value': FLEET_TAG_VALUE
            }
        ]
    )

    return response['TargetGroups'][0]


def register_targets(
        target_group_arn: str, 
        target_ip: str, 
        target_port: int
    ):
    # add the instances to the target groups
    elbv2_client.register_targets(
        TargetGroupArn=target_group_arn,
        Targets=[
            {
                'Id': target_ip,
                'Port': target_port
            }
        ]
    )


def create_listener(
        alb_arn: str, 
        target_groups: list[str]
    ) -> dict:

    target_groups_definition = []

    for target_group in target_groups:
        target_groups_definition.append(
            {
                'TargetGroupArn': target_group['TargetGroupArn'],
                'Weight': 10
            }
        )

    default_actions = {
        'Type': 'forward',
        'ForwardConfig': {
            'TargetGroups': target_groups_definition
        }
    }

    listener_response = elbv2_client.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol='HTTP',
        Port=ALB_LISTENER_PORT,
        DefaultActions=[default_actions]
    )
    
    logger.debug(listener_response['Listeners'][0])

    return listener_response['Listeners'][0]



def create_load_balancer(alb_name: str) -> dict:

    # create the ALB
    create_lb = elbv2_client.create_load_balancer(
        Name=alb_name,
        Subnets=ALB_SUBNET_IDS,
        SecurityGroups=ALB_SECURITY_GROUPS,
        Scheme='internet-facing',
        Tags=[
            {
                'Key': ALB_TAG_KEY,
                'Value': ALB_TAG_VALUE
            },
            {
                'Key': constants_service.ALB_CREATION_TAG_KEY,
                'Value': constants_service.ALB_CREATION_TAG_VALUE
            },
            {
                'Key': FLEET_TAG_KEY,
                'Value': FLEET_TAG_VALUE
            }
        ],
        Type='application',
        IpAddressType='ipv4'
    )
    
    # wait for the ALB to be available
    waiter = elbv2_client.get_waiter('load_balancer_available')
    waiter.wait(Names=[alb_name])
    
    alb_arn = create_lb['LoadBalancers'][0]['LoadBalancerArn']
    alb_dns_name = create_lb['LoadBalancers'][0]['DNSName']
    alb_hosted_zone = create_lb['LoadBalancers'][0]['CanonicalHostedZoneId']
    
    logger.debug(f"alb_arn: {alb_arn}")
    logger.debug(f"alb_name: {alb_name}")
    logger.debug(f"alb_dns_name: {alb_dns_name}")
    logger.debug(f"alb_hosted_zone: {alb_hosted_zone}")
    
    return create_lb['LoadBalancers'][0]


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))
        
    # create objects for tracking task progress
    event['create_alb_operation'] = {}
    event['create_alb_operation']['input'] = {}
    event['create_alb_operation']['output'] = {}
    
    try:

        # abort if add alb operations are suspended
        response = ssm_client.get_parameter(
            Name=SUSPEND_ADD_ALB_PARAM_NAME
        )

        is_suspended = response['Parameter']['Value']

        if is_suspended.lower() == "true":
            logger.info("Add alb operations are suspended.")
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        # get details from previous stage
        event['add_alb_event'] = {}
        event['add_alb_event']['output'] = {}
        event['add_alb_event']['output']['execution_arn'] = event['state_machine_arn']
        event['add_alb_event']['output']['execution_name'] = event['state_machine_name']
        event['add_alb_event']['output']['triggered_by'] = event['triggered_by']

        # add stage specific details
        event['create_alb_operation']['input']['operator'] = OPERATOR
        event['create_alb_operation']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['create_alb_operation']['output']['operation_required'] = True

        suffix_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k = 5))
        alb_name = f'fleet-alb-{suffix_id}'

        # create the load balancer
        load_balancer = create_load_balancer(alb_name)
        alb_arn = load_balancer['LoadBalancerArn']
        alb_dns_name = load_balancer['DNSName']
        alb_hosted_zone = load_balancer['CanonicalHostedZoneId']

        target_groups = []

        ecs_task_target_ips = get_ecs_task_ips(
            cluster_arn=ECS_CLUSTER_ARN
        )

        # create the target groups and register the targets
        for idx, target_ip in enumerate(ecs_task_target_ips):
            suffix_number = "{:02d}".format(idx+1)
            target_group =  create_target_group(f'fleet-alb-tg-{suffix_id}-{suffix_number}')
            target_group_arn = target_group['TargetGroupArn']
            register_targets(target_group_arn, target_ip, TARGET_GROUP_PORT)
            target_groups.append(target_group)

        # create the ALB listener with the target groups
        listener = create_listener(alb_arn, target_groups)

        event['create_alb_operation']['output']['status'] = "COMPLETED"
        event['create_alb_operation']['output']['alb_arn'] = alb_arn
        event['create_alb_operation']['output']['alb_name'] = alb_name
        event['create_alb_operation']['output']['alb_dns_name'] = alb_dns_name
        event['create_alb_operation']['output']['alb_hosted_zone'] = alb_hosted_zone
        event['create_alb_operation']['output']['load_balancer'] = load_balancer
        
        # creation time is of datetime which can't be serialized by the state machine so convert it to string
        creation_time = event['create_alb_operation']['output']['load_balancer']['CreatedTime']
        event['create_alb_operation']['output']['load_balancer']['CreatedTime'] = creation_time.strftime("%m/%d/%Y, %H:%M:%S")
        
        event['create_alb_operation']['output']['target_groups'] = target_groups
        
        logger.debug(event)
        logger.debug(json.dumps(event, indent=2))
        

        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'create_alb_operation'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['create_alb_operation']['output']['status'] = "ERROR"
        event['create_alb_operation']['output']['hasError'] = True
        event['create_alb_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'create_alb_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['create_alb_operation']['output']['status'] = "ERROR"
        event['create_alb_operation']['output']['hasError'] = True
        event['create_alb_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'create_alb_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
