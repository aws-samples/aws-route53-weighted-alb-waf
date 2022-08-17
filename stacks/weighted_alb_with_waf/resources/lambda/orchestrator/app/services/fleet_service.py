import boto3, os, logging
import datetime
from ..services.constants_service import ConstantsService

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

class FleetService:

    client = boto3.client('elbv2')

    TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
    ALB_TAG_KEY = os.environ['ALB_TAG_KEY']
    ALB_TAG_VALUE = os.environ['ALB_TAG_VALUE']

    constants_service = ConstantsService()


    def __filter_load_balancers_by_group(
            self, 
            load_balancers: list[dict]
        ) -> list[str]:
        if len(load_balancers) == 0:
            return []

        albs_with_tag = set(())

        response = self.client.describe_tags(
            ResourceArns=load_balancers
        )

        for load_balancer in response['TagDescriptions']:
            for tag in load_balancer['Tags']:
                if 'Key' in tag and tag['Key'] == self.ALB_TAG_KEY:
                    if 'Value' in tag and tag['Value'] == self.ALB_TAG_VALUE:
                        albs_with_tag.add(load_balancer['ResourceArn'])

        # remove duplicates while maintaining list type
        albs_with_tag = list(albs_with_tag)
        
        return albs_with_tag


    def __filter_load_balancers_by_dynamic_only(
            self, 
            load_balancers: list[dict]
        ) -> list[str]:
        if len(load_balancers) == 0:
            return []

        albs_with_tag = set(())

        response = self.client.describe_tags(
            ResourceArns=load_balancers
        )

        for load_balancer in response['TagDescriptions']:
            for tag in load_balancer['Tags']:
                if 'Key' in tag and tag['Key'] == self.constants_service.ALB_CREATION_TAG_KEY:
                    if 'Value' in tag and tag['Value'] == self.constants_service.ALB_CREATION_TAG_VALUE:
                        albs_with_tag.add(load_balancer['ResourceArn'])
        
        # remove duplicates while maintaining list type
        albs_with_tag = list(albs_with_tag)

        return albs_with_tag

    def __get_load_balancers_by_arn(
            self, 
            load_balancer_arns: list[str]
        ) -> list[str]:
        return self.client.describe_load_balancers(
            LoadBalancerArns=load_balancer_arns
        )['LoadBalancers']


    def get_load_balancers(
            self, 
            filter: str
        ) -> list[str]:
        load_balancers = set(())

        paginator = self.client.get_paginator('describe_load_balancers')
        page_iterator = paginator.paginate()
        for page in page_iterator:
            for load_balancer in page['LoadBalancers']:
                load_balancers.add(load_balancer['LoadBalancerArn'])
            
        # remove duplicates while maintaining list type
        load_balancers = list(load_balancers)

        if filter.upper() == self.constants_service.FILTER_BY_GROUP:
            filtered_load_balancers = self.__filter_load_balancers_by_group(load_balancers)
            return self.__get_load_balancers_by_arn(filtered_load_balancers)
        elif filter.upper() == self.constants_service.FILTER_BY_CREATION_DYNAMIC:
            filtered_load_balancers = self.__filter_load_balancers_by_dynamic_only(load_balancers)
            return self.__get_load_balancers_by_arn(filtered_load_balancers)
        else:
            raise ValueError(f"Invalid filter value. Should be one of; {self.constants_service.FILTER_BY_GROUP} or {self.constants_service.FILTER_BY_CREATION_DYNAMIC}. ")