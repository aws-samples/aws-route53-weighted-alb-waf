#!/usr/bin/env python

"""
    dynamic_resource_cleanup.py:
    Lambda Function that is used to destroy the dynamic (non CDK managed)
    AWS Resources that are created during ALB Scale-Out operations.
"""

import json
import logging

import boto3

from ..remove_alb import (
    delete_load_balancer, 
    disassociate_alb_to_waf,
    remove_alb_from_route_53
)
from ..services.constants_service import ConstantsService
from ..services.fleet_service import FleetService
from ..services.statemachine_service import StateMachineService

# boto3 clients
stepfunctions_client = boto3.client('stepfunctions')

# services
fleet_service = FleetService()
constants_service = ConstantsService()
statemachine_service = StateMachineService()

def lambda_handler(event, context):
    # set logging
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # print the event details
    logger.debug(json.dumps(event, indent=2))
    
    state_machine_event = {
        "state_machine_arn": "INVOKED_FROM_DYNAMIC_RESOURCE_CLEANER",
        "state_machine_name": "INVOKED_FROM_DYNAMIC_RESOURCE_CLEANER",
        "triggered_by": "DYNAMIC_RESOURCE_CLEANER"
    }

    dynamic_alb_fleet = fleet_service.get_load_balancers(constants_service.FILTER_BY_CREATION_DYNAMIC)
    logger.debug("Pre-processing dynamic resources")
    logger.debug(dynamic_alb_fleet)

    for dynamic_alb in dynamic_alb_fleet:
        logger.info(f"Performing cleanup for dynamic alb: {dynamic_alb['LoadBalancerArn']}")
        logger.debug(dynamic_alb)

        logger.info(f"Deleting load balancer: {dynamic_alb['LoadBalancerArn']}")
        delete_load_balancer.lambda_handler(state_machine_event, None)

        logger.info(f"Disassociate load balancer from WAF")
        delete_alb_operation_event = {
            'output': {
                'alb_arn': dynamic_alb['LoadBalancerArn'],
                'alb_name': dynamic_alb['LoadBalancerName'],
                'alb_dns_name': dynamic_alb['DNSName'],
                'alb_hosted_zone': dynamic_alb['CanonicalHostedZoneId']
            }
        }
        waf_disassociate_event = {
            'disassociate_from_waf_operation':{
                'input':{
                    'loadbalancer_arn': dynamic_alb['LoadBalancerArn']
                }
            },
            'delete_alb_operation': delete_alb_operation_event
        }
        disassociate_alb_to_waf.lambda_handler(waf_disassociate_event, None)

        logger.info(f"Remove load balancer from Route53")
        remove_alb_from_route_53.lambda_handler(
            {
                'delete_alb_operation': delete_alb_operation_event
            }, 
            None)

        logger.info(f"Completed dynamic cleanup for: {dynamic_alb['LoadBalancerArn']}")


    output = {
        'PhysicalResourceId': "dynamic-resource-cleaner-id",
        'Data': {
            'ExecutionArn': "INVOKED_FROM_DYNAMIC_RESOURCE_CLEANER"
        }
    }
    logger.info("Output: " + json.dumps(output))
    return output
