import os
import boto3
import botocore.exceptions
import json
import logging
import traceback
import datetime
from ..services.notifier_service import NotifierService
from ..services.statemachine_service import StateMachineService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
stepfunctions_client = boto3.client('stepfunctions')
ssm_client = boto3.client('ssm')

# get env vars
REMOVE_ALB_STATE_MACHINE_ARN = os.environ['REMOVE_ALB_STATE_MACHINE_ARN']
REMOVE_ALB_STATE_MACHINE_NAME = os.environ['REMOVE_ALB_STATE_MACHINE_NAME']
SUSPEND_REMOVE_ALB_PARAM_NAME = os.environ['SUSPEND_REMOVE_ALB_PARAM_NAME']

# static vars
OPERATOR = "REMOVE_ALB"

# services
notifier_service = NotifierService()
statemachine_service = StateMachineService()


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # create objects for tracking task progress
    event['remove_alb'] = {}
    event['remove_alb']['input'] = {}
    event['remove_alb']['output'] = {}

    try:

        # abort if remove-alb operations are suspended
        response = ssm_client.get_parameter(
            Name=SUSPEND_REMOVE_ALB_PARAM_NAME
        )

        is_suspended = response['Parameter']['Value']

        if is_suspended.lower() == "true":
            logger.info("Remove alb operations are suspended.")
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        # fail safe - only start a new state machine if there are no active remove alb operations
        if statemachine_service.is_operation_in_progress() == True:
            logger.info("A remove alb operation is in progress, aborting this attempt.")
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        # add stage specific details
        event['remove_alb']['input']['operator'] = OPERATOR
        event['remove_alb']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")

        response = stepfunctions_client.start_execution(
            stateMachineArn=REMOVE_ALB_STATE_MACHINE_ARN,
             input="{\"state_machine_arn\" : \"" + REMOVE_ALB_STATE_MACHINE_ARN + "\", \"state_machine_name\" : \"" + REMOVE_ALB_STATE_MACHINE_NAME + "\", \"triggered_by\" : \"remove_alb_ALARM\"}"
        )

        event['remove_alb']['output']['status'] = "COMPLETED"
        event['remove_alb']['output']['execution_arn'] = response['executionArn']
        
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

        event['remove_alb']['output']['status'] = "ERROR"
        event['remove_alb']['output']['hasError'] = True
        event['remove_alb']['output']['errorMessage'] = str(e)
        
        subject = f"ERROR: {OPERATOR} operation failed with errors.",
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'add-alb'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['remove_alb']['output']['status'] = "ERROR"
        event['remove_alb']['output']['hasError'] = True
        event['remove_alb']['output']['errorMessage'] = str(e)
        
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