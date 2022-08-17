import os
import boto3
import botocore.exceptions
import json
import logging
import traceback
import datetime
from ..services.notifier_service import NotifierService
from ..services.fleet_service import FleetService
from ..services.constants_service import ConstantsService
from ..services.statemachine_service import StateMachineService
from ..services.monitor_service import MonitorService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# get env vars
WAF_WEB_ACL_ARN = os.environ['WAF_WEB_ACL_ARN']
PRIVATE_ZONE_ID = os.environ['ROUTE_53_PRIVATE_ZONE_ID']
ROUTE_53_ALB_DNS_NAME = os.environ['ROUTE_53_ALB_DNS_NAME']
FLEET_INTEGRITY_ENFORCER_RATE = int(os.environ['INTEGRITY_ENFORCER_RATE'])

# static vars
OPERATOR = "FLEET_MONITOR"
AUTO_REMEDIATE_MSG = (
    "This issue can be AUTO_REMEDIATED and should be automatically resolved during the next execution of the " +
    f"Integrity Enforcer which runs every {FLEET_INTEGRITY_ENFORCER_RATE} minutes."
)
NO_REMEDIATE_MSG = (
    "This type of issue can NOT be AUTO_REMEDIATED and will require manual actions to resolve."
)

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()
statemachine_service = StateMachineService()
monitor_service = MonitorService()

# boto3 clients
wafv2_client = boto3.client('wafv2')
route53_client = boto3.client('route53')
cloudwatch_client = boto3.client('cloudwatch')


def check_albs_not_impaired(albs: list[str]):

    albs_active_impaired = set(())

    for alb in albs:
        if alb['State']['Code'] == "active_impaired":
            albs_active_impaired.add(alb['LoadBalancerArn'])
            logger.error(f"ALB {alb['LoadBalancerArn']} has state 'active_impaired'. Expected state is 'active'")
        elif alb['State']['Code'] == "active":
            logger.info(f"ALB {alb['LoadBalancerArn']} has expected state 'active")

    # remove duplicates while maintaining list type
    albs_active_impaired = list(albs_active_impaired)

    if len(albs_active_impaired) > 0:
        msg = (
            f"The following ALBs {','.join(albs_active_impaired)} have state 'active_impaired'. " +
            "Expected state is 'active'. " +
            NO_REMEDIATE_MSG
        )
        raise ValueError(msg)


def check_albs_associated_to_waf(albs: list[str]):
    disassociated_waf_albs = monitor_service.get_albs_disassociated_from_waf(albs=albs)

    if len(disassociated_waf_albs) > 0:
        msg = (
            f"The following ALBs {','.join(disassociated_waf_albs)} are not associated " +
            f"to WAF {WAF_WEB_ACL_ARN}. All ALBs must be associated to WAF. " +
            AUTO_REMEDIATE_MSG
        )
        raise ValueError(msg)


def check_weighted_resource_set_association(albs: list[str]):
    missing_records = monitor_service.get_missing_resource_set_records(albs)

    if len(missing_records) > 0:
        msg = (
          f"The following ALBs {missing_records} have not been associated " +
          f"to the Route 53 weighted resource set: {ROUTE_53_ALB_DNS_NAME}. " +
          AUTO_REMEDIATE_MSG 
        )
        raise ValueError(msg)


def check_weights_of_resource_set(albs: list[str]):
    resource_records = route53_client.list_resource_record_sets(
        HostedZoneId=PRIVATE_ZONE_ID,
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
            msg = (
                "There are multiple ALB resources in the Route53 record set. " +
                f"The following ALBs Route 53 records: {records_to_adjust} " +
                "should have their WEIGHT value set 0 to support distribute load balancing to the ALBs. " +
                AUTO_REMEDIATE_MSG
            )
            raise ValueError(msg)

    elif len(resource_records['ResourceRecordSets']) == 1:
        # if we just have one alb record make sure the value if greater than 0
        for record in resource_records['ResourceRecordSets']:
         if record['Weight'] == 0:
            msg = (
                "There is a single ALB resource in the Route53 record set. " +
                f"This record must have its WEIGHT value set to > 0. " +
                AUTO_REMEDIATE_MSG
            )
            raise ValueError(msg)
    else:
        # if we no alb records
        msg = (
            "There are no records found in the route 53 resource set. " +
            f"{len(albs)} records were expected for the folllowing albs: {','.join(alb['LoadBalancerArn'] for alb in albs)} " +
            AUTO_REMEDIATE_MSG
        )
        raise ValueError(msg)


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    try:

        # skip monitor check if an operation is in progress
        if statemachine_service.is_operation_in_progress() == True:
            response_message = { "monitor_status": "operation in progress"}

            return {
                'statusCode': 200,
                'body': json.dumps(response_message, indent=2),
                'headers': {'Content-Type': 'application/json'}
            }


        albs = fleet_service.get_load_balancers(constants_service.FILTER_BY_GROUP)

        logger.debug("Albs to monitor")
        logger.debug(albs)

        # check 01 - verify that no albs are impaired
        check_albs_not_impaired(albs)

        # check 03 - verify albs are associated to WAF
        check_albs_associated_to_waf(albs)

        # check 04 - verify that albs are associated to route 53 weighted resource set
        check_weighted_resource_set_association(albs)

        # check 05 - verify that the resources have the correct weight
        check_weights_of_resource_set(albs)

        response_message = { "monitor_status": "ok"}

        return {
            'statusCode': 200,
            'body': json.dumps(response_message, indent=2),
            'headers': {'Content-Type': 'application/json'}
        }
    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')
        
        subject = f"Unexpected error in ALB monitor process."
        error_timestamp = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        
        template_attributes = {}
        template_attributes['error_message'] = str(e)
        template_attributes['error_timestamp'] = error_timestamp

        notifier_service.send_monitor_failure(
            subject=subject, 
            template_attributes=template_attributes,
            is_specific_failure=True
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')
        
        subject = f"Unexpected error in ALB monitor process."
        error_timestamp = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        
        template_attributes = {}
        template_attributes['error_message'] = str(e)
        template_attributes['error_timestamp'] = error_timestamp

        notifier_service.send_monitor_failure(
            subject=subject, 
            template_attributes=template_attributes,
            is_specific_failure=False
        )
        
        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }