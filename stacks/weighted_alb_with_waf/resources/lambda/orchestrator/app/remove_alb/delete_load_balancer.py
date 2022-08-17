import os
import boto3
import botocore.exceptions
import json
import logging
import datetime
import traceback
import time
from ..services.notifier_service import NotifierService
from ..services.fleet_service import FleetService
from ..services.constants_service import ConstantsService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
client = boto3.client('elbv2')
ssm_client = boto3.client('ssm')

# get env vars
SUSPEND_REMOVE_ALB_PARAM_NAME = os.environ['SUSPEND_REMOVE_ALB_PARAM_NAME']

# static vars
OPERATOR = "DELETE_LOAD_BALANCER"

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()


def get_target_groups(alb_arn: str) -> dict:
    tg_response = client.describe_target_groups(
        LoadBalancerArn=alb_arn,
        PageSize=100
    )

    return tg_response['TargetGroups']


def delete_target_groups(alb_target_groups: list[str]) -> list[str]:
    deleted_target_groups = set(())

    tg_deleted = False
    in_use_max_attempts = 30
    in_use_count = 0

    while tg_deleted == False and in_use_count < in_use_max_attempts:
        try:
            for target_group in alb_target_groups:
                client.delete_target_group(
                    TargetGroupArn=target_group['TargetGroupArn']
                )
                deleted_target_groups.add(
                    target_group['TargetGroupArn']
                )
            
            # mark the tg as deleted to break the while loop
            tg_deleted = True
        except client.exceptions.ResourceInUseException as e:
            logger.error(f"Can't delete target group as it is attached to a LoadBalancer that has not been fully deleted. Target groups to be deleted: {','.join(tg['TargetGroupArn'] for tg in alb_target_groups)}. Target groups actually deleted: {','.join(deleted_target_groups)}")
            logger.info(f"Retrying target group delete: attempt {in_use_count} of {in_use_max_attempts}")

            # increment the counter
            in_use_count += in_use_count + 1

            # sleep for 10 seconds before retrying
            time.sleep(10)

    # remove duplicates while maintaining list type
    deleted_target_groups = list(deleted_target_groups)
            
    if len(deleted_target_groups) != len(alb_target_groups):
        raise ValueError(f"Some target groups could not be deleted from an ALB LoadBalancer. Target groups to be deleted: {','.join(tg['TargetGroupArn'] for tg in alb_target_groups)}. Target groups actually deleted: {','.join(deleted_target_groups)}")
    
    return deleted_target_groups


def delete_load_balancer(alb_arn: str):

    # delete the ALB
    delete_response = client.delete_load_balancer(
        LoadBalancerArn=alb_arn
    )
    
    # wait for the ALB to be deleted
    waiter = client.get_waiter('load_balancers_deleted')
    waiter.wait(LoadBalancerArns=[alb_arn])


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # create objects for tracking task progress
    event['delete_alb_operation'] = {}
    event['delete_alb_operation']['input'] = {}
    event['delete_alb_operation']['output'] = {}
    event['delete_alb_operation']['output']['load_balancer'] = {}

    try:

        # abort if remove alb operations are suspended
        response = ssm_client.get_parameter(
            Name=SUSPEND_REMOVE_ALB_PARAM_NAME
        )

        is_suspended = response['Parameter']['Value']

        logger.debug(f"is_suspended == {is_suspended}")

        if is_suspended.lower() == "true":
            logger.info("Remove alb operations are suspended.")
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        # get details from previous stage
        event['remove_alb_event'] = {}
        event['remove_alb_event']['output'] = {}
        event['remove_alb_event']['output']['execution_arn'] = event['state_machine_arn']
        event['remove_alb_event']['output']['execution_name'] = event['state_machine_name']
        event['remove_alb_event']['output']['triggered_by'] = event['triggered_by']

        # add stage specific details
        event['delete_alb_operation']['input']['operator'] = OPERATOR
        event['delete_alb_operation']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['delete_alb_operation']['output']['operation_required'] = True
        
        deletable_albs = fleet_service.get_load_balancers(constants_service.FILTER_BY_CREATION_DYNAMIC)

        logger.debug("ALBs that have been identified for deletion.")
        logger.debug(deletable_albs)
        logger.debug(len(deletable_albs))

        if len(deletable_albs) == 0:
            logger.info(f"No available load balancers have been identified for deletion.")
            event['delete_alb_operation']['output']['operation_required'] = False
            return {
                'statusCode': 200,
                'body': event,
                'headers': {'Content-Type': 'application/json'}
            }

        alb_arn = deletable_albs[0]['LoadBalancerArn']
        alb_name = deletable_albs[0]['LoadBalancerName']
        alb_dns_name = deletable_albs[0]['DNSName']
        alb_hosted_zone = deletable_albs[0]['CanonicalHostedZoneId']

        # grab the target groups associated with the alb
        target_groups = get_target_groups(alb_arn)
        logger.debug("Target groups to be deleted:")
        logger.debug(target_groups)

        # delete the load balancer
        delete_load_balancer(alb_arn)

        # delete the target groups
        deleted_target_groups = delete_target_groups(target_groups)
        logger.debug("Deleted target groups:")
        logger.debug(deleted_target_groups)

        event['delete_alb_operation']['output']['status'] = "COMPLETED"
        event['delete_alb_operation']['output']['alb_arn'] = alb_arn
        event['delete_alb_operation']['output']['alb_name'] = alb_name
        event['delete_alb_operation']['output']['alb_dns_name'] = alb_dns_name
        event['delete_alb_operation']['output']['alb_hosted_zone'] = alb_hosted_zone
        event['delete_alb_operation']['output']['target_groups'] = target_groups
       
        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'delete_alb_operation'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
    
    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['delete_alb_operation']['output']['status'] = "ERROR"
        event['delete_alb_operation']['output']['hasError'] = True
        event['delete_alb_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'delete_alb_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['delete_alb_operation']['output']['status'] = "ERROR"
        event['delete_alb_operation']['output']['hasError'] = True
        event['delete_alb_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'delete_alb_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
