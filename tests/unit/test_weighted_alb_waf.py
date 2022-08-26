import json

import pytest
from expects import expect

from cdk_expects_matcher.CdkMatchers import have_resource, ANY_VALUE, contain_metadata_path
import tests.utils.base_test_case as tc
from stacks.weighted_alb_with_waf.weighted_alb_with_waf_stack import WeightedAlbWithWAFStack
from utils.CdkUtils import CdkUtils


@pytest.fixture(scope="class")
def weighted_alb_waf_stack(request):
    request.cls.cfn_template = tc.BaseTestCase.load_stack_template(WeightedAlbWithWAFStack.__name__)


@pytest.mark.usefixtures('synth', 'weighted_alb_waf_stack')
class TestWeightedAlbWafStack(tc.BaseTestCase):
    """
        Test case for WeightedAlbWaf
    """

    config = CdkUtils.get_project_settings()

    ##################################################
    ## <START> AWS VPC element tests
    ##################################################
    def test_vpc_created(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.vpc, "weighted-alb-vpc"
            )
        )

    def test_vpc_subnets_count(self):
        assert json.dumps(self.cfn_template).count('\"AWS::EC2::Subnet\"') == 4

    def test_vpc_subnets_rt_assoc_count(self):
        assert json.dumps(self.cfn_template).count('\"AWS::EC2::SubnetRouteTableAssociation\"') == 4

    def test_vpc_subnets_nat_gw_count(self):
        assert json.dumps(self.cfn_template).count('\"AWS::EC2::NatGateway\"') == 2

    def test_vpc_subnets_eip_count(self):
        assert json.dumps(self.cfn_template).count('\"AWS::EC2::EIP\"') == 2
    ##################################################
    ## </END> AWS VPC element tests
    ##################################################

    ##################################################
    ## <START> EC2 Security Group tests
    ##################################################
    def test_no_admin_permissions(self):
        assert json.dumps(self.cfn_template).count(':iam::aws:policy/AdministratorAccess') == 0

    def test_alb_security_group_created(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ec2_security_group, "alb-security-group")
        )

    def test_ecs_security_group_created(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ec2_security_group, "ecs-sg")
        )

    def test_ecs_task_role_created(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.iam_role, "ecs-task-role")
        )

    def test_ecs_security_group_ingress_rules(self):
        expect(self.cfn_template).to(have_resource(
            self.ec2_security_group,
            {
                "SecurityGroupEgress": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "Allow all outbound traffic by default",
                        "IpProtocol": "-1"
                    }
                ],
                "VpcId": {
                    "Ref": ANY_VALUE
                }
            }
        ))


    def test_alb_security_group_ingress_rules(self):
        expect(self.cfn_template).to(have_resource(
            self.ec2_security_group,
            {
                "SecurityGroupEgress": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "Allow all outbound traffic by default",
                        "IpProtocol": "-1"
                    }
                ],
                "SecurityGroupIngress": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "HTTP Access to ALB",
                        "FromPort": 80,
                        "IpProtocol": "tcp",
                        "ToPort": 80
                    }
                ],
                "VpcId": {
                    "Ref": ANY_VALUE
                }
            }
        ))
    ##################################################
    ## </END> EC2 Security Group tests
    ##################################################

    ##################################################
    ## <START> KMS tests
    ##################################################
    def test_kms_key_rotation_created(self):
        expect(self.cfn_template).to(have_resource(self.kms_key, {
            "EnableKeyRotation": True
        }))

    def test_kms_key_alias_created(self):
        expect(self.cfn_template).to(have_resource(self.kms_alias, {
            "AliasName": "alias/orchestrator-key"
        }))

    def test_kms_key_created(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.kms_key, "orchestrator-key"
            )
        )
    ##################################################
    ## </END> AWS KMS tests
    ##################################################

    ##################################################
    ## <START> AWS Cloudwatch LogGroups tests
    ##################################################
    def test_add_alb_loggroup_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.cw_log_group, "add-alb-statemachine-loggroup"
            )
        )

    def test_remove_alb_loggroup_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.cw_log_group, "remove-alb-statemachine-loggroup"
            )
        )
    ##################################################
    ## </END> AWS Cloudwatch LogGroups tests
    ##################################################

    ##################################################
    ## <START> AWS Elastic Load Balancer tests
    ##################################################
    def test_alb_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.elastic_load_balancer, "alb-for-ecs"
            )
        )

    def test_alb_listener_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.elastic_load_balancer_listener, "alb-http-port-80-listener"
            )
        )

    def test_alb_targetgroup_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.elastic_load_balancer_targetgroup, "target-group-http"
            )
        )

    def test_alb_targetgroup_rules(self):
        expect(self.cfn_template).to(have_resource(
            self.elastic_load_balancer_targetgroup,
            {
                "HealthCheckPath": "/",
                "HealthCheckProtocol": "HTTP",
                "Port": 80,
                "Protocol": "HTTP",
                "TargetGroupAttributes": [
                {
                    "Key": "stickiness.enabled",
                    "Value": "false"
                }
                ],
                "TargetType": "ip",
                "VpcId": {
                    "Ref": ANY_VALUE
                }
            }
        ))
    ##################################################
    ## </END> AWS Elastic Load Balancer tests
    ##################################################

    ##################################################
    ## <START> Route 53 tests
    ##################################################
    def test_hosted_zone_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.route53_hosted_zone, "fleet-hosted-zone"))

    def test_hosted_zone_definition(self):
        expect(self.cfn_template).to(have_resource(
            self.route53_hosted_zone,
            {
                "Name": f"{self.config['dnsDomain']['zoneName']}.",
                "VPCs": [
                    {
                    "VPCId": {
                    "Ref": ANY_VALUE
                    },
                    "VPCRegion": ANY_VALUE
                    }
                ]
            }
        ))
    ##################################################
    ## </END> Route 53 tests
    ##################################################

    ##################################################
    ## <START> State Machine tests
    ##################################################
    def test_add_alb_state_machine_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.state_machine, "add-alb-statemachine"))

    def test_remove_alb_state_machine_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.state_machine, "remove-alb-statemachine"))
    ##################################################
    ## </END> State Machine tests
    ##################################################

    ##################################################
    ## <START> Lambda function test
    ##################################################
    def test_remove_alb_executor_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "remove-alb-executor-lambda-role"))

    def test_remove_alb_executor_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "remove-alb-executor-lambda"))

    def test_monitor_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "monitor-lambda-role"))

    def test_monitor_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "monitor-lambda"))

    def test_integrity_enforcer_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "integrity-enforcer-lambda-role"))

    def test_integrity_enforcer_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "integrity-enforcer-lambda"))

    def test_create_loadbalancer_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "create-loadbalancer-lambda-role"))

    def test_create_loadbalancer_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "create-loadbalancer-lambda"))

    def test_associate_alb_to_waf_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "associate-alb-to-waf-lambda-role"))

    def test_associate_alb_to_waf_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "associate-alb-to-waf-lambda"))

    def test_add_alb_to_route53_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "add-alb-to-route53-lambda-role"))

    def test_add_alb_to_route53_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "add-alb-to-route53-lambda"))
        
    def test_delete_loadbalancer_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "delete-loadbalancer-lambda-role"))

    def test_delete_loadbalancer_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "delete-loadbalancer-lambda"))

    def test_disassociate_alb_to_waf_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "disassociate-alb-to-waf-lambda-role"))

    def test_disassociate_alb_to_waf_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "disassociate-alb-to-waf-lambda"))

    def test_remove_alb_from_route53_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "remove-alb-from-route53-lambda-role"))

    def test_remove_alb_from_route53_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "remove-alb-from-route53-lambda"))

    def test_alb_arn_modifier_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "alb-arn-modifier-lambda-role"))

    def test_alb_arn_modifier_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "alb-arn-modifier-lambda"))

    def test_alb_arn_modifier_custom_resource(self):
        expect(self.cfn_template).to(contain_metadata_path(self.custom_cfn_resource, "alb-arn-modifier-lambda-customresource"))

    def test_dynamic_resource_cleaner_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "dynamic-resource-cleaner-lambda-role"))

    def test_dynamic_resource_cleaner_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "dynamic-resource-cleaner-lambda"))

    def test_add_alb_executor_lambda_role(self):
        expect(self.cfn_template).to(contain_metadata_path(self.iam_role, "add-alb-executor-lambda-role"))

    def test_add_alb_executor_lambda(self):
        expect(self.cfn_template).to(contain_metadata_path(self.lambda_, "add-alb-executor-lambda"))
    ##################################################
    ## </END> Lambda function test
    ##################################################


    ##################################################
    ## <START> SSM Parameter tests
    ##################################################
    def test_add_alb_suspend_param_ssm(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ssm_parameter, "add-alb-suspend-param"))

    def test_remove_alb_suspend_param_ssm(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ssm_parameter, "remove-alb-suspend-param"))
    ##################################################
    ## </END> SSM Parameter tests
    ##################################################


    ##################################################
    ## <START> SNS tests
    ##################################################
    def test_orchestrator_add_alb_topic_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.sns_topic, "orchestrator-add-alb-topic"))

    def test_orchestrator_remove_alb_topic_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.sns_topic, "orchestrator-remove-alb-topic"))

    def test_orchestrator_notifier_topic_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.sns_topic, "orchestrator-notifier-topic"))
    ##################################################
    ## </END> SNS tests
    ##################################################


    ###################################################
    # <START> ECS Clusters and Tasks tests
    ###################################################
    def test_ecs_cluster_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ecs_cluster, "alb-backend-cluster"))

    def test_ecs_service_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ecs_service, "weighted_alb_ecs_service"))

    def test_ecs_task_definition_exists(self):
        expect(self.cfn_template).to(
            contain_metadata_path(self.ecs_task_definition, "ecs-task"))
    ###################################################
    # </END> ECS Clusters and Tasks tests
    ###################################################