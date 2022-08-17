import os
import boto3
import botocore.exceptions
import json
import time
import logging
import datetime
import traceback
from ..services.notifier_service import NotifierService
from ..services.fleet_service import FleetService
from ..services.constants_service import ConstantsService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
client = boto3.client('wafv2')

# get env vars
WAF_WEB_ACL_ARN = os.environ['WAF_WEB_ACL_ARN']

# static vars
OPERATOR = "DISASSOCIATE_ALB_TO_WAF"

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()

client = boto3.client('wafv2')


def disassociate_waf_from_alb(alb_arn: str):
    waf_albs = client.list_resources_for_web_acl(
        WebACLArn=WAF_WEB_ACL_ARN,
        ResourceType='APPLICATION_LOAD_BALANCER'
    )['ResourceArns']

    if alb_arn in waf_albs:
        try:
            time.sleep(15)
            client.disassociate_web_acl(
                ResourceArn=alb_arn
            )
        except Exception as e:
            logger.error(f"Error attempting to disassociate ALB: {alb_arn} from WAF: {WAF_WEB_ACL_ARN}")

        waf_albs = client.list_resources_for_web_acl(
            WebACLArn=WAF_WEB_ACL_ARN,
            ResourceType='APPLICATION_LOAD_BALANCER'
        )['ResourceArns']

        if alb_arn in waf_albs:
            logger.error(f"ALB {alb_arn} is still associated to WAF {WAF_WEB_ACL_ARN} despite an ALB delete operation. Current ALBs associated to WAF: {','.join(waf_albs)}")
            raise ValueError(f"ALB {alb_arn} is still associated to WAF {WAF_WEB_ACL_ARN} despite an ALB delete operation. Current ALBs associated to WAF: {','.join(waf_albs)}")
        else:
            logger.info(f"ALB {alb_arn} has been correctly disassociated from WAF {WAF_WEB_ACL_ARN}. Current ALBs associated to WAF: {','.join(waf_albs)}")
    else:
        logger.info(f"ALB {alb_arn} has been correctly disassociated from WAF {WAF_WEB_ACL_ARN}. Current ALBs associated to WAF: {','.join(waf_albs)}")


def list_resources_for_web_acl() -> list[str]:
    return client.list_resources_for_web_acl(
        WebACLArn=WAF_WEB_ACL_ARN,
        ResourceType='APPLICATION_LOAD_BALANCER'
    )['ResourceArns']


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    # create objects for tracking task progress
    event['disassociate_from_waf_operation'] = {}
    event['disassociate_from_waf_operation']['input'] = {}
    event['disassociate_from_waf_operation']['output'] = {}

    try:

        # get details from previous stage
        alb_arn = event['delete_alb_operation']['output']['alb_arn']

        # add stage specific details
        event['disassociate_from_waf_operation']['input']['operator'] = OPERATOR
        event['disassociate_from_waf_operation']['input']['timestamp'] = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        event['disassociate_from_waf_operation']['input']['loadbalancer_arn'] = alb_arn
        event['disassociate_from_waf_operation']['input']['waf_alb_acl_id'] = WAF_WEB_ACL_ARN
        
        # associate the load balancer to WAF
        disassociate_waf_from_alb(alb_arn)

        event['disassociate_from_waf_operation']['output']['status'] = "COMPLETED"
        
        subject = f"{OPERATOR} operation COMPLETED."
        notifier_service.send_notification(
            subject, 
            "operation_success.template", 
            event,
            'disassociate_from_waf_operation'
        )

        return {
            'statusCode': 200,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
    
    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['disassociate_from_waf_operation']['output']['status'] = "ERROR"
        event['disassociate_from_waf_operation']['output']['hasError'] = True
        event['disassociate_from_waf_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'disassociate_from_waf_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')

        event['disassociate_from_waf_operation']['output']['status'] = "ERROR"
        event['disassociate_from_waf_operation']['output']['hasError'] = True
        event['disassociate_from_waf_operation']['output']['errorMessage'] = str(e)
        
        subject = f"{OPERATOR} operation FAILED."
        notifier_service.send_notification(
            subject, 
            "operation_fail.template", 
            event,
            'disassociate_from_waf_operation'
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
