#!/usr/bin/env python

"""
    associate_alb_to_waf.py:
    Lambda handler that is invoked by an AWS Step Functions
    State Machine as part of an ALB Scale-Out operation.
    This handler ensures that the newly created ALB
    is associated to AWS WAF.
"""

import datetime
import json
import logging
import os
import traceback

import boto3
import botocore.exceptions

from ..services.notifier_service import NotifierService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
client = boto3.client('wafv2')

# get env vars
WAF_WEB_ACL_ARN = os.environ['WAF_WEB_ACL_ARN']

# static vars
OPERATOR = "ASSOCIATE_ALB_TO_WAF"

# services
notifier_service = NotifierService()

client = boto3.client('wafv2')


def associate_waf_to_alb(alb_arn: str):
    client.associate_web_acl(
        WebACLArn=WAF_WEB_ACL_ARN,
        ResourceArn=alb_arn
    )


def list_resources_for_web_acl() -> list[str]:
    return client.list_resources_for_web_acl(
        WebACLArn=WAF_WEB_ACL_ARN,
        ResourceType='APPLICATION_LOAD_BALANCER'
    )['ResourceArns']


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # get details from previous stage
    alb_arn = event['create_alb_operation']['output']['alb_arn']

    # create objects for tracking task progress
    event['associate_to_waf_operation'] = {}
    event['associate_to_waf_operation']['input'] = {}
    event['associate_to_waf_operation']['output'] = {}

    try:

        # add stage specific details
        event['associate_to_waf_operation']['input']['operator'] = OPERATOR
        event['associate_to_waf_operation']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['associate_to_waf_operation']['input']['loadbalancer_arn'] = alb_arn
        event['associate_to_waf_operation']['input']['waf_alb_acl_id'] = WAF_WEB_ACL_ARN

        # associate the load balancer to WAF
        associate_waf_to_alb(alb_arn)

        # list the alb arns associated with the WAF
        associated_arns = list_resources_for_web_acl()

        event['associate_to_waf_operation']['output']['status'] = "COMPLETED"
        event['associate_to_waf_operation']['output']['albs_associated_to_waf'] = associated_arns
        
        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'associate_to_waf_operation'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['associate_to_waf_operation']['output']['status'] = "ERROR"
        event['associate_to_waf_operation']['output']['hasError'] = True
        event['associate_to_waf_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'associate_to_waf_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['associate_to_waf_operation']['output']['status'] = "ERROR"
        event['associate_to_waf_operation']['output']['hasError'] = True
        event['associate_to_waf_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'associate_to_waf_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
