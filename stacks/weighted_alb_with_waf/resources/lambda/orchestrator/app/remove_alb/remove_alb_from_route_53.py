#!/usr/bin/env python

"""
    remove_alb_to_route_53.py:
    Lambda handler that is invoked by an AWS Step Functions
    State Machine as part of an ALB Scale-In operation.
    This handler ensures that the newly created ALB
    is removed from the Weighted Resource Set of the Route53
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
OPERATOR = "REMOVE_ALB_FROM_ROUTE_53"

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()

client = boto3.client('route53')


def adjust_weighted_record(alb_hosted_zone: str):
    resource_records = client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    records_to_adjust = []

    # if we have multiple records in the weighted resource set
    # ensure that all records have a weight of 0 for distributed
    # load balancing
    if len(resource_records['ResourceRecordSets']) > 1:
        for record in resource_records['ResourceRecordSets']:
            if record['Weight'] != 0:
                records_to_adjust.append(record)

        if len(records_to_adjust) > 0:
            for record_to_adjust in records_to_adjust:
                change_info = client.change_resource_record_sets(
                    HostedZoneId=HOSTED_ZONE_ID,
                    ChangeBatch={
                        'Comment': 'Removed ALB',
                        'Changes': [
                            {
                                'Action': 'UPSERT',
                                'ResourceRecordSet': {
                                    'Name': ROUTE_53_ALB_DNS_NAME,
                                    'Type': 'A',
                                    'SetIdentifier': record_to_adjust['SetIdentifier'],
                                    'Weight': 0,
                                    'AliasTarget': {
                                        'HostedZoneId': alb_hosted_zone,
                                        'DNSName': record_to_adjust['AliasTarget']['DNSName'],
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
    elif len(resource_records['ResourceRecordSets']) == 1:
        # we only have one load balancer in the fleet, so we need to set the weight to a value
        # greater than 0
        record_to_adjust = resource_records['ResourceRecordSets'][0]
        change_info = client.change_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID,
            ChangeBatch={
                'Comment': 'Removed ALB',
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': ROUTE_53_ALB_DNS_NAME,
                            'Type': 'A',
                            'SetIdentifier': record_to_adjust['SetIdentifier'],
                            'Weight': 255,
                            'AliasTarget': {
                                'HostedZoneId': alb_hosted_zone,
                                'DNSName': record_to_adjust['AliasTarget']['DNSName'],
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


def delete_weighted_record(
        alb_dns_name: str, 
        alb_hosted_zone: str
    ) -> dict:

    resource_records = client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    record_weight = 0
    identifier = ""
    dns_name = ""

    for record in resource_records['ResourceRecordSets']:
        if record['AliasTarget']['DNSName'].lower().startswith(alb_dns_name.lower()):
            record_weight = record['Weight']
            identifier = record['SetIdentifier']
            dns_name = record['AliasTarget']['DNSName']
            break

    change_info = client.change_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        ChangeBatch={
            'Comment': 'Removed ALB',
            'Changes': [
                {
                    'Action': 'DELETE',
                    'ResourceRecordSet': {
                        'Name': ROUTE_53_ALB_DNS_NAME,
                        'Type': 'A',
                        'SetIdentifier': identifier,
                        'Weight': record_weight,
                        'AliasTarget': {
                            'HostedZoneId': alb_hosted_zone,
                            'DNSName': dns_name,
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
    event['remove_alb_from_route53'] = {}
    event['remove_alb_from_route53']['input'] = {}
    event['remove_alb_from_route53']['output'] = {}

    try:

        # get details from previous stage
        alb_arn = event['delete_alb_operation']['output']['alb_arn']
        alb_name = event['delete_alb_operation']['output']['alb_name']
        alb_dns_name = event['delete_alb_operation']['output']['alb_dns_name']
        alb_hosted_zone = event['delete_alb_operation']['output']['alb_hosted_zone']

        # add stage specific details
        event['remove_alb_from_route53']['input']['operator'] = OPERATOR
        event['remove_alb_from_route53']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['remove_alb_from_route53']['input']['alb_arn'] = alb_arn
        event['remove_alb_from_route53']['input']['alb_name'] = alb_name
        event['remove_alb_from_route53']['input']['alb_dns_name'] = alb_dns_name
        event['remove_alb_from_route53']['input']['alb_hosted_zone'] = alb_hosted_zone

        record_change_info = delete_weighted_record(alb_dns_name, alb_hosted_zone)

        # after deletion, check if we need to adjust resource record weighting values
        adjust_weighted_record(alb_hosted_zone)

        event['remove_alb_from_route53']['output']['status'] = "COMPLETED"
        record_change_info['ChangeInfo']['Status'] = "DELETED"
        record_change_info['ChangeInfo']['SubmittedAt'] = record_change_info['ChangeInfo']['SubmittedAt'].strftime("%m/%d/%Y, %H:%M:%S")
        event['remove_alb_from_route53']['output']['change_info'] = record_change_info['ChangeInfo']
  
        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'remove_alb_from_route53'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
    
    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['remove_alb_from_route53']['output']['status'] = "ERROR"
        event['remove_alb_from_route53']['output']['hasError'] = True
        event['remove_alb_from_route53']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'remove_alb_from_route53'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['remove_alb_from_route53']['output']['status'] = "ERROR"
        event['remove_alb_from_route53']['output']['hasError'] = True
        event['remove_alb_from_route53']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'remove_alb_from_route53'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
