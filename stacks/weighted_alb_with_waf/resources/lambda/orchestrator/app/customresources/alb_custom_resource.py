#!/usr/bin/env python

"""
    alb_custom_resource.py:
    CloudFormation Custom Resource handler that is used
    to obtain the ALB Name from a CDK generated ALB ARN.
"""

import json
import logging


def lambda_handler(event, context):
    # set logging
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # print the event details
    logger.debug(json.dumps(event, indent=2))

    props = event['ResourceProperties']
    alb_arn = props['LoadBalancerArn']

    logger.info(f"Incoming alb_arn == {alb_arn}")

    alb_arn_name = f"{alb_arn.split('/',1)[1]}"

    logger.info(f"Modified alb_arn_name == {alb_arn_name}")

    output = {
        'PhysicalResourceId': f"alb-arn-modifier-id",
        'Data': {
            'AlbArnName': alb_arn_name
        }
    }
    logger.info("Output: " + json.dumps(output))
    return output
