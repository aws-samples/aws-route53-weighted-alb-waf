#!/usr/bin/env python

"""
    add_alb_to_route_53.py:
    Lambda handler that is invoked by an AWS Step Functions
    State Machine as part of an ALB Scale-Out operation.
    This handler ensures that the newly created ALB
    is added to the Weighted Resource Set of the Route53
    private hosted zone.
"""

import datetime
import json
import logging
import os
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
client = boto3.client('wafv2')

# get env vars
HOSTED_ZONE_ID = os.environ['ROUTE_53_PRIVATE_ZONE_ID']
ROUTE_53_ALB_DNS_NAME = os.environ['ROUTE_53_ALB_DNS_NAME']

# static vars
OPERATOR = "ADD_ALB_TO_ROUTE_53"
WEIGHTED_RECORD_BASE = 255

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()

client = boto3.client('route53')


def get_resource_record_weight() -> int:
    resource_records = client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    if len(resource_records['ResourceRecordSets']) > 0:
        return 0
    else:
        return 255


def create_weighted_record(
        alb_dns_name: str, 
        alb_hosted_zone: str, 
        alb_name: str
    ) -> dict:

    resource_records = client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    record_exists = False

    for record in resource_records['ResourceRecordSets']:
        if 'AliasTarget' in record:
            if record['AliasTarget']['DNSName'] == alb_dns_name:
                record_exists = True

    change_info = client.change_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        ChangeBatch={
            'Comment': 'Adjusted ALB',
            'Changes': [
                {
                    'Action': 'CREATE' if record_exists else 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': ROUTE_53_ALB_DNS_NAME,
                        'Type': 'A',
                        'SetIdentifier': alb_name,
                        'Weight': get_resource_record_weight(),
                        'AliasTarget': {
                            'HostedZoneId': alb_hosted_zone,
                            'DNSName': alb_dns_name,
                            'EvaluateTargetHealth': True
                        }
                    }
                }
            ]
        }
    )

    waiter = client.get_waiter('resource_record_sets_changed')
    waiter.wait(
    Id=change_info['ChangeInfo']['Id'],
    WaiterConfig={
            'Delay': 10,
            'MaxAttempts': 30
        }
    )

    return change_info


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # create objects for tracking task progress
    event['add_alb_to_route53'] = {}
    event['add_alb_to_route53']['input'] = {}
    event['add_alb_to_route53']['output'] = {}
   
    try:

        # get details from previous stage
        alb_arn = event['create_alb_operation']['output']['alb_arn']
        alb_name = event['create_alb_operation']['output']['alb_name']
        alb_dns_name = event['create_alb_operation']['output']['alb_dns_name']
        alb_hosted_zone = event['create_alb_operation']['output']['alb_hosted_zone']

        # add stage specific details
        event['add_alb_to_route53']['input']['operator'] = OPERATOR
        event['add_alb_to_route53']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['add_alb_to_route53']['input']['alb_arn'] = alb_arn
        event['add_alb_to_route53']['input']['alb_name'] = alb_name
        event['add_alb_to_route53']['input']['alb_dns_name'] = alb_dns_name
        event['add_alb_to_route53']['input']['alb_hosted_zone'] = alb_hosted_zone

        # increment the weight value
        record_change_info = create_weighted_record(alb_dns_name, alb_hosted_zone, alb_name)

        event['add_alb_to_route53']['output']['status'] = "COMPLETED"
        record_change_info['ChangeInfo']['Status'] = "COMPLETED"
        event['add_alb_to_route53']['output']['change_info'] = record_change_info['ChangeInfo']

        # creation time is of datetime which can't be serialized by the state machine so convert it to string
        submitted_at = event['add_alb_to_route53']['output']['change_info']['SubmittedAt']
        event['add_alb_to_route53']['output']['change_info']['SubmittedAt'] = submitted_at.strftime("%m/%d/%Y, %H:%M:%S")
  
        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'add_alb_to_route53'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['add_alb_to_route53']['output']['status'] = "ERROR"
        event['add_alb_to_route53']['output']['hasError'] = True
        event['add_alb_to_route53']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'add_alb_to_route53'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['add_alb_to_route53']['output']['status'] = "ERROR"
        event['add_alb_to_route53']['output']['hasError'] = True
        event['add_alb_to_route53']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'add_alb_to_route53'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
