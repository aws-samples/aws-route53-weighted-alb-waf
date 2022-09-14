#!/usr/bin/env python

"""
    notifier_service.py:
    Provides functions for generating and sending notification messages.
"""

import logging
import os
from os.path import abspath, dirname

import boto3
import jinja2
import yaml

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)


class NotifierService:
    """
        Provides functions for generating and sending notification messages.
    """

    client = boto3.client('sns')

    TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
    AWS_REGION = os.environ['AWS_REGION_NAME']
    AWS_ACCOUNT_ID = os.environ['AWS_ACCOUNT_ID']

    TEMPLATE_DIR = dirname(dirname(abspath(__file__)))
    TEMPLATE_LOADER = jinja2.FileSystemLoader(searchpath=f"{TEMPLATE_DIR}/templates")
    TEMPLATE_ENV = jinja2.Environment(
        loader=TEMPLATE_LOADER,
        autoescape=jinja2.select_autoescape(
            default_for_string=True,
            default=True
        )
    )


    def get_notification_template(
            self, 
            event: dict, 
            event_id: str
        ) -> dict:
        # notification template attributes
        template_attributes = {}

        template_attributes['aws_region'] = self.AWS_REGION
        template_attributes['aws_account_id'] = self.AWS_ACCOUNT_ID

        if event_id in event:
            template_attributes['formatted_event'] = yaml.dump(event[event_id])
            try:
                template_attributes['operator'] = event[event_id]['input']['operator']
            except:
                template_attributes['operator'] = event_id
        else:
            template_attributes['formatted_event'] = yaml.dump(event)
            template_attributes['operator'] = event_id
        
        if 'add_alb_event' in event:
            template_attributes['state_machine_arn'] = event['add_alb_event']['output']['execution_arn']
            template_attributes['state_machine_name'] = event['add_alb_event']['output']['execution_name']
        elif 'remove_alb_event' in event:
            template_attributes['state_machine_arn'] = event['remove_alb_event']['output']['execution_arn']
            template_attributes['state_machine_name'] = event['remove_alb_event']['output']['execution_name']

        if 'errorMessage' in event[event_id]['output']:
            template_attributes['error_message'] = event[event_id]['output']['errorMessage']

        logger.debug(template_attributes)

        return template_attributes

    
    def send_notification(
            self, 
            subject: str, 
            template_file: str, 
            event: dict, 
            event_id: str
        ):
        template = self.TEMPLATE_ENV.get_template(template_file)
        template_attributes = self.get_notification_template(event, event_id)
        try:
            message = template.render(vars=template_attributes)
        except Exception as e:
            message = "\n".join(
                [
                        f"An error occured rendering the email template {template_file}.",
                        f"Error message: {str(e)}",
                        f"Event state in YAML:",
                        yaml.dump(event)
                ]
            )

        self.client.publish(
            TopicArn = self.TOPIC_ARN,
            Subject = subject,
            Message = message
        )


    def send_monitor_failure(
            self, 
            subject: str, 
            template_attributes: str, 
            is_specific_failure: bool
        ):
        template = self.TEMPLATE_ENV.get_template('monitor_fail_generic.template')
        if is_specific_failure:
            template = self.TEMPLATE_ENV.get_template('monitor_fail_specific.template')
            
        try:
            message = template.render(vars=template_attributes)
        except Exception as e:
            message = "\n".join(
                [
                        f"An error occured rendering the email template {template}.",
                        f"Error message: {str(e)}",
                        f"Event state in YAML:",
                        yaml.dump(template_attributes)
                ]
            )

        self.client.publish(
            TopicArn = self.TOPIC_ARN,
            Subject = subject,
            Message = message
        )
