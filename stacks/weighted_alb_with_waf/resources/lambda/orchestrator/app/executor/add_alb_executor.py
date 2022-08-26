#!/usr/bin/env python

"""
    add_alb_executor.py:
    Lambda Function that provides an endpoint through which
    the AWS Step Functions State Machine for ALB Scale-Out
    operations is invoked.
"""

import datetime
import json
import logging
import os
import traceback

import boto3
import botocore.exceptions

from ..services.notifier_service import NotifierService
from ..services.statemachine_service import StateMachineService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
stepfunctions_client = boto3.client('stepfunctions')
ssm_client = boto3.client('ssm')

# get env vars
ADD_ALB_STATE_MACHINE_ARN = os.environ['ADD_ALB_STATE_MACHINE_ARN']
ADD_ALB_STATE_MACHINE_NAME = os.environ['ADD_ALB_STATE_MACHINE_NAME']
SUSPEND_ADD_ALB_PARAM_NAME = os.environ['SUSPEND_ADD_ALB_PARAM_NAME']

# static vars
OPERATOR = "ADD_ALB"

# services
notifier_service = NotifierService()
statemachine_service = StateMachineService()


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # create objects for tracking task progress
    event['add_alb'] = {}
    event['add_alb']['input'] = {}
    event['add_alb']['output'] = {}

    try:

        # abort if add-alb operations are suspended
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

        # fail safe - only start a new state machine if there are no active current operations
        if statemachine_service.is_operation_in_progress() == True:
            logger.info("A add alb operation is in progress, aborting this attempt.")
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        # add stage specific details
        event['add_alb']['input']['operator'] = OPERATOR
        event['add_alb']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")

        response = stepfunctions_client.start_execution(
            stateMachineArn=ADD_ALB_STATE_MACHINE_ARN,
            input="{\"state_machine_arn\" : \"" + ADD_ALB_STATE_MACHINE_ARN + "\", \"state_machine_name\" : \"" + ADD_ALB_STATE_MACHINE_NAME + "\", \"triggered_by\" : \"ADD_ALB_ALARM\"}"
        )

        event['add_alb']['output']['status'] = "COMPLETED"
        event['add_alb']['output']['execution_arn'] = response['executionArn']
        
        subject = f"{OPERATOR} operation executed.",
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'add_alb'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['add_alb']['output']['status'] = "ERROR"
        event['add_alb']['output']['hasError'] = True
        event['add_alb']['output']['errorMessage'] = str(e)
        
        subject = f"ERROR: {OPERATOR} operation failed with errors.",
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'add_alb'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['add_alb']['output']['status'] = "ERROR"
        event['add_alb']['output']['hasError'] = True
        event['add_alb']['output']['errorMessage'] = str(e)
        
        subject = f"ERROR: {OPERATOR} operation failed with errors.",
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'add_alb'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
