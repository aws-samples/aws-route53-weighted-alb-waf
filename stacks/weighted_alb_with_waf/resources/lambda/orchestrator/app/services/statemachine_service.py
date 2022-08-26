#!/usr/bin/env python

"""
    statemachine_service.py:
    Provides functions for querying AWS Step Functions State Machines.
"""

import logging
import os

import boto3

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
stepfunctions_client = boto3.client('stepfunctions')

class StateMachineService:
    """
        Provides functions for querying AWS Step Functions State Machines.
    """

    # get env vars
    ADD_ALB_STATE_MACHINE_ARN = os.environ['ADD_ALB_STATE_MACHINE_ARN']
    ADD_ALB_STATE_MACHINE_NAME = os.environ['ADD_ALB_STATE_MACHINE_NAME']
    REMOVE_ALB_STATE_MACHINE_ARN = os.environ['REMOVE_ALB_STATE_MACHINE_ARN']
    REMOVE_ALB_STATE_MACHINE_NAME = os.environ['REMOVE_ALB_STATE_MACHINE_NAME']

    def is_operation_in_progress(self) -> bool:

         # fail safe - only start a new state machine if there are no active current operations
        add_alb_execution = stepfunctions_client.list_executions(
            stateMachineArn=self.ADD_ALB_STATE_MACHINE_ARN,
            statusFilter='RUNNING'
        )

        if len(add_alb_execution['executions']) > 0:
            logger.info("An add alb operation is in progress, aborting this attempt.")
            logger.debug(add_alb_execution)
            return True

        remove_alb_execution = stepfunctions_client.list_executions(
            stateMachineArn=self.REMOVE_ALB_STATE_MACHINE_ARN,
            statusFilter='RUNNING'
        )

        if len(remove_alb_execution['executions']) > 0:
            logger.info("A remover alb operation is in progress, aborting this attempt.")
            logger.debug(remove_alb_execution)
            return True
    
        return False

