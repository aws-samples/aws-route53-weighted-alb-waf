import os
import json

from aws_cdk import (
    core,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as albv2,
    aws_wafv2 as wafv2,
    aws_route53 as route53,
    aws_kms as kms,
    aws_lambda,
    aws_iam as iam,
    aws_sns as sns,
    aws_ssm as ssm,
    aws_sns_subscriptions,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_stepfunctions as stepfunctions,
    aws_stepfunctions_tasks as stepfunctions_tasks,
    aws_ecs as ecs,
    aws_logs as logs,
    custom_resources
)

from utils.CdkUtils import CdkUtils


class WeightedAlbWithWAFStack(core.Stack):

    LAMBDA_PRINCIPAL: iam.IPrincipal = iam.ServicePrincipal(service=f'lambda.{core.Aws.URL_SUFFIX}')
    CLOUDWATCH_PRINCIPAL: iam.IPrincipal = iam.ServicePrincipal(service=f'cloudwatch.{core.Aws.URL_SUFFIX}')
    SNS_PRINCIPAL: iam.IPrincipal = iam.ServicePrincipal(service=f'sns.{core.Aws.URL_SUFFIX}')

    def __init__(self, scope: core.Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = CdkUtils.get_project_settings()
        self.orchestrator_config = CdkUtils.get_project_settings()['orchestrator']

        ##################################################
        ## <START> Network prequisites
        ##################################################

        # create the vpc
        self.vpc = ec2.Vpc(
            self,
            "weighted-alb-vpc",
            cidr=config["vpc"]["cidr"],
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="sweighted-alb-subnet-public",
                    cidr_mask=config["vpc"]["subnets"]["mask"],
                    subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    name="weighted-alb-subnet-private",
                    cidr_mask=config["vpc"]["subnets"]["mask"],
                    subnet_type=ec2.SubnetType.PRIVATE
                )
            ]
        )

        ##################################################
        ## </END> Network prequisites
        ##################################################


        ###################################################
        # <START> Create ALB, ROUTE 53 and WAF
        ###################################################

        self.alb_sg = ec2.SecurityGroup(
            scope=self,
            id="alb-security-group",
            allow_all_outbound=True,
            vpc=self.vpc
        )

        self.alb_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(80),
            description="HTTP Access to ALB"
        )

        alb = albv2.ApplicationLoadBalancer(
            scope=self,
            id="alb-for-ecs",
            load_balancer_name="fleet-alb-1",
            vpc=self.vpc,
            security_group=self.alb_sg,
            internet_facing=True
        )


        # add alb tags
        core.Tags.of(alb).add(self.orchestrator_config['fleetTag']['key'], self.orchestrator_config['fleetTag']['value'])
        core.Tags.of(alb).add(self.orchestrator_config['elbTag']['key'], self.orchestrator_config['elbTag']['value'])
        core.Tags.of(alb).add("FLEET_ALB_CREATION", "STATIC")

        target_group_http = albv2.ApplicationTargetGroup(
            self,
            "target-group-http",
            port=80,
            vpc=self.vpc,
            protocol=albv2.ApplicationProtocol.HTTP,
            target_type=albv2.TargetType.IP
        )

        # Health check for containers to check they were deployed correctly
        target_group_http.configure_health_check(
            path="/",
            protocol=albv2.Protocol.HTTP
        )

        alb_listener = alb.add_listener(
            id="alb-http-port-80-listener",
            open=True,
            port=80,
            protocol=albv2.ApplicationProtocol.HTTP
        )

        alb_listener.add_target_groups(
            "alb-listener-target-group",
            target_groups=[target_group_http]
        )

        self.dns_hosted_zone = route53.PrivateHostedZone(
            scope=self,
            id="fleet-hosted-zone",
            zone_name=config["dnsDomain"]["zoneName"],
            vpc=self.vpc
        )

        route53.CfnRecordSet(
            scope=self,
            id="albRecordSet",
            name=f"alb.{config['dnsDomain']['zoneName']}",
            type="A",
            alias_target=route53.CfnRecordSet.AliasTargetProperty(
                dns_name=alb.load_balancer_dns_name,
                hosted_zone_id=alb.load_balancer_canonical_hosted_zone_id,
                evaluate_target_health=True
            ),
            weight=255,
            hosted_zone_id=self.dns_hosted_zone.hosted_zone_id,
            set_identifier=alb.load_balancer_name
        )

        # Associate WAF to ALB
        self.web_acl = wafv2.CfnWebACL(
            scope_=self,
            id="ManagedWebACL-1",
            scope="REGIONAL",
            name="Fleet-Web-ACL",
            description="Example Rule",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="WAF-Metrics",
                sampled_requests_enabled=True
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-AWSManagedRulesCommonRuleSet",
                    priority=0,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name="AWSManagedRulesCommonRuleSet"
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWS-AWSManagedRulesCommonRuleSet",
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-AWSManagedRulesKnownBadInputsRuleSet",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name="AWSManagedRulesKnownBadInputsRuleSet"
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWS-AWSManagedRulesKnownBadInputsRuleSet",
                    )
                )
            ],
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={})
        )

        wafv2.CfnWebACLAssociation(
            scope=self,
            id="Waf2AlbAssociation",
            resource_arn=alb.load_balancer_arn,
            web_acl_arn=self.web_acl.attr_arn
        )

        ###################################################
        # </END> Create ALB, ROUTE 53 and WAF
        ###################################################

        ###################################################
        # <START> KMS Key
        ###################################################

        orchestrator_key = kms.Key(
            self,
            "orchestrator-key",
            removal_policy=core.RemovalPolicy.DESTROY,
            alias="alias/orchestrator-key",
            description="KMS Key used for Orchestrator",
            enable_key_rotation=True
        )

        ###################################################
        # </END> KMS Key
        ###################################################


        ###################################################
        # <START> SSM Parameters
        ###################################################

        self.suspend_add_alb_param = ssm.StringParameter(
            self, 
            id="add-alb-suspend-param",
            parameter_name='/weighted-alb-with-waf/add-alb-suspend',
            string_value='false',
            description='boolean indicating if add alb operations should be suspended',
            type=ssm.ParameterType.STRING,
            tier=ssm.ParameterTier.STANDARD
        )


        self.suspend_remove_alb_param = ssm.StringParameter(
            self, 
            id="remove-alb-suspend-param",
            parameter_name='/weighted-alb-with-waf/remove-alb-suspend',
            string_value='false',
            description='boolean indicating if remove alb operations should be suspended',
            type=ssm.ParameterType.STRING,
            tier=ssm.ParameterTier.STANDARD
        )
        ###################################################
        # </END> SSM Parameters
        ###################################################


        ###################################################
        # <START> SNS Topics and Subscriptions
        ###################################################

        self.add_alb_topic = sns.Topic(
            self, "orchestrator-add-alb-topic",
            topic_name="OrchestratorAddAlbTopic",
            master_key=orchestrator_key
        )

        self.remove_alb_topic = sns.Topic(
            self, "orchestrator-remove-alb-topic",
            topic_name="OrchestratorRemoveAlbTopic",
            master_key=orchestrator_key
        )

        self.notification_topic = sns.Topic(
            self, "orchestrator-notifier-topic",
            topic_name="OrchestratorNotifierTopic",
            master_key=orchestrator_key
        )

        orchestrator_key.grant_encrypt_decrypt(self.SNS_PRINCIPAL)
        orchestrator_key.grant_encrypt_decrypt(self.CLOUDWATCH_PRINCIPAL)

        self.notification_subscription = sns.Subscription(
            self, "NotifierSubscription",
            topic=self.notification_topic,
            endpoint=self.orchestrator_config["notification"]['target'],
            protocol=sns.SubscriptionProtocol.EMAIL
        )

        ###################################################
        # </END> SNS Topics and Subscriptions
        ###################################################


        ###################################################
        # <START> ECS Clusters and Tasks
        ###################################################

        self.ecs_cluster = ecs.Cluster(
            self,
            "alb-backend-cluster",
            vpc=self.vpc
        )

        ecs_task_role = iam.Role(
            self, 
            "ecs-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ]
        )

        self.ecs_task_definition =ecs.TaskDefinition(
            self,
            "ecs-task",
            family="task",
            compatibility=ecs.Compatibility.EC2_AND_FARGATE,
            cpu="512",
            memory_mib="1024",
            network_mode=ecs.NetworkMode.AWS_VPC,
            task_role=ecs_task_role
        )

        ecs_container = self.ecs_task_definition.add_container(
            "http-echo-container",
            image=ecs.ContainerImage.from_registry("mendhak/http-https-echo"),
            memory_limit_mib=1024,
            logging=ecs.LogDriver.aws_logs(stream_prefix="ecs-task-http-echo")
        )

        ecs_container.add_port_mappings(
            ecs.PortMapping(
                container_port=80
            )
        )

        # Security groups to allow connections from the application load balancer to the fargate containers
        ecs_sg = ec2.SecurityGroup(
            self, 
            "ecs-sg",
            vpc=self.vpc,
            allow_all_outbound=True
        )

        ecs_sg.connections.allow_from(
            self.alb_sg,
            port_range=ec2.Port.all_tcp(),
            description="Application load balancer"
        )

        # The ECS Service used for deploying tasks 
        ecs_service = ecs.FargateService(
            self, 
            "weighted_alb_ecs_service",
            cluster=self.ecs_cluster,
            desired_count=2,
            task_definition=self.ecs_task_definition,
            security_group=ecs_sg,
            assign_public_ip=False
        )
        
        # add to a target group so make containers discoverable by the application load balancer
        ecs_service.attach_to_application_target_group(target_group_http)

        ###################################################
        # </END> ECS Clusters and Tasks
        ###################################################


        ###################################################
        # <START> State Machine Lambda functions
        ###################################################

        # /** ADD ALB FUNCTIONS

        create_load_balancer_lambda = self.get_create_load_balancer_lambda()
        associate_alb_to_waf_lambda = self.get_associate_alb_to_waf_lambda()
        add_alb_to_route53_lambda = self.get_add_alb_to_route53_lambda()

        orchestrator_key.grant_encrypt_decrypt(create_load_balancer_lambda)
        orchestrator_key.grant_encrypt_decrypt(associate_alb_to_waf_lambda)
        orchestrator_key.grant_encrypt_decrypt(add_alb_to_route53_lambda)

        # /** REMOVE ALB FUNCTIONS

        delete_load_balancer_lambda = self.get_delete_load_balancer_lambda()
        disassociate_alb_to_waf_lambda = self.get_disassociate_alb_to_waf_lambda()
        remove_alb_from_route53_lambda = self.get_remove_alb_from_route53_lambda()

        orchestrator_key.grant_encrypt_decrypt(delete_load_balancer_lambda)
        orchestrator_key.grant_encrypt_decrypt(disassociate_alb_to_waf_lambda)
        orchestrator_key.grant_encrypt_decrypt(remove_alb_from_route53_lambda)

        ###################################################
        # </END> State Machine Lambda functions
        ###################################################


        ###################################################
        # <START> Custom Resources
        ###################################################

        # /** START ALB MODIFIER **/

        alb_arn_modifier_lambda_role = iam.Role(
            scope=self,
            id="alb-arn-modifier-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )

        alb_arn_modifier_lambda = aws_lambda.Function(
            scope=self,
            id="alb-arn-modifier-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(f"{os.path.dirname(__file__)}/resources/lambda/orchestrator"),
            handler="app.customresources.alb_custom_resource.lambda_handler",
            role=alb_arn_modifier_lambda_role,
            timeout=core.Duration.minutes(1)
        )

        # Provider that invokes the ami distribution lambda function
        alb_arn_modifier_provider = custom_resources.Provider(
            self,
            'alb-arn-modifier-lambda-customresourceprovider',
            on_event_handler=alb_arn_modifier_lambda
        )

        # The custom resource
        alb_arn_modifier_custom_resource = core.CustomResource(
            self,
            'alb-arn-modifier-lambda-customresource',
            service_token=alb_arn_modifier_provider.service_token,
            properties = {
                'LoadBalancerArn': alb.load_balancer_arn
            }
        )

        alb_arn_modifier_custom_resource.node.add_dependency(alb)

        # The result obtained from the output of custom resource
        alb_arn_name = core.CustomResource.get_att_string(alb_arn_modifier_custom_resource, attribute_name='AlbArnName')

        # /** END ALB MODIFIER **/

        ###################################################
        # </END> Custom Resources
        ###################################################

        ###################################################
        # <START> Step functions
        ###################################################

        # /** BEGIN ADD ALB STATE MACHINE **/

        # create log group for State Machine
        add_alb_state_machine_log_group = logs.LogGroup(
            self, 'add-alb-statemachine-loggroup',
            log_group_name=f'/aws/vendedlogs/states/addAlb',
            removal_policy=core.RemovalPolicy.DESTROY
        )

        add_alb_step01_create_load_balancer = stepfunctions_tasks.LambdaInvoke(
            self,
            "Create and add new ALB to group",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=create_load_balancer_lambda
        )

        add_alb_step01_required_choice = stepfunctions.Choice(
            self,
            "Is a Add-ALB operation required?",
            input_path="$",
            output_path="$"
        )

        add_alb_step01_result_choice = stepfunctions.Choice(
            self,
            "Was the ALB created successfully?",
            input_path="$",
            output_path="$"
        )

        add_alb_step_02_associate_alb_to_waf = stepfunctions_tasks.LambdaInvoke(
            self,
            "Assocate ALB to WAF",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=associate_alb_to_waf_lambda
        )

        add_alb_step02_choice = stepfunctions.Choice(
            self,
            "Was ALB associated to WAF successfully?",
            input_path="$",
            output_path="$"
        )

        add_alb_step_03_add_alb_to_route53 = stepfunctions_tasks.LambdaInvoke(
            self,
            "Add ALB to Route53",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=add_alb_to_route53_lambda
        )

        add_alb_step03_choice = stepfunctions.Choice(
            self,
            "Was ALB added to Route53 successfully?",
            input_path="$",
            output_path="$"
        )

        add_alb_step_success = stepfunctions.Succeed(
            self,
            "Add ALB Lifecycle success."
        )

        add_alb_step_fail = stepfunctions.Fail(
            self,
            "Add ALB Lifecycle failure."
        )

        add_alb_step01_required_choice.when(stepfunctions.Condition.boolean_equals('$.create_alb_operation.output.operation_required', False),
                                  add_alb_step_success).otherwise(add_alb_step01_result_choice)

        add_alb_step01_result_choice.when(stepfunctions.Condition.string_equals('$.create_alb_operation.output.status', "ERROR"),
                                  add_alb_step_fail).otherwise(add_alb_step_02_associate_alb_to_waf)

        add_alb_step_02_associate_alb_to_waf.next(add_alb_step02_choice)

        add_alb_step02_choice.when(stepfunctions.Condition.string_equals('$.associate_to_waf_operation.output.status', "ERROR"),
                            add_alb_step_fail).otherwise(add_alb_step_03_add_alb_to_route53)

        add_alb_step_03_add_alb_to_route53.next(add_alb_step03_choice)

        add_alb_step03_choice.when(stepfunctions.Condition.string_equals('$.add_alb_to_route53.output.status', "ERROR"),
                    add_alb_step_fail).otherwise(add_alb_step_success)

        # step functions state machine
        add_alb_state_machine = stepfunctions.StateMachine(
            self, 
            "add-alb-statemachine",
            state_machine_name="AddAlbStateMachine",
            timeout=core.Duration.minutes(120),
            definition=add_alb_step01_create_load_balancer.next(add_alb_step01_required_choice),
            logs=stepfunctions.LogOptions(
                destination=add_alb_state_machine_log_group,
                level=stepfunctions.LogLevel.ALL
            )
        )

        # /**END ADD ALB STATE MACHINE **/


        # /** BEGIN REMOVE ALB STATE MACHINE **/

        # create log group for State Machine
        remove_alb_state_machine_log_group = logs.LogGroup(
            self, 'remove-alb-statemachine-loggroup',
            log_group_name='/aws/vendedlogs/states/removeAlb',
            removal_policy=core.RemovalPolicy.DESTROY
        )

        remove_alb_step01_delete_load_balancer = stepfunctions_tasks.LambdaInvoke(
            self,
            "Remove ALB from group",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=delete_load_balancer_lambda
        )

        remove_alb_step01_required_choice = stepfunctions.Choice(
            self,
            "Is a Remove ALB operation required?",
            input_path="$",
            output_path="$"
        )

        remove_alb_step01_result_choice = stepfunctions.Choice(
            self,
            "Was the ALB removed successfully?",
            input_path="$",
            output_path="$"
        )

        remove_alb_step_02_disassociate_alb_to_waf = stepfunctions_tasks.LambdaInvoke(
            self,
            "Disassocate ALB from WAF",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=disassociate_alb_to_waf_lambda
        )

        remove_alb_step02_choice = stepfunctions.Choice(
            self,
            "Was ALB disassociated from WAF successfully?",
            input_path="$",
            output_path="$"
        )

        remove_alb_step_03_remove_alb_from_route53 = stepfunctions_tasks.LambdaInvoke(
            self,
            "Remove ALB from Route53",
            input_path="$",
            output_path="$.Payload.body",
            lambda_function=remove_alb_from_route53_lambda
        )

        remove_alb_step03_choice = stepfunctions.Choice(
            self,
            "Was ALB removed from Route53 successfully?",
            input_path="$",
            output_path="$"
        )

        remove_alb_step_success = stepfunctions.Succeed(
            self,
            "Remove ALB Lifecycle success."
        )

        remove_alb_step_fail = stepfunctions.Fail(
            self,
            "Remove ALB Lifecycle failure."
        )

        remove_alb_step01_required_choice.when(stepfunctions.Condition.boolean_equals('$.delete_alb_operation.output.operation_required', False),
                                  remove_alb_step_success).otherwise(remove_alb_step01_result_choice)

        remove_alb_step01_result_choice.when(stepfunctions.Condition.string_equals('$.delete_alb_operation.output.status', "ERROR"),
                                  remove_alb_step_fail).otherwise(remove_alb_step_02_disassociate_alb_to_waf)

        remove_alb_step_02_disassociate_alb_to_waf.next(remove_alb_step02_choice)

        remove_alb_step02_choice.when(stepfunctions.Condition.string_equals('$.disassociate_from_waf_operation.output.status', "ERROR"),
                            remove_alb_step_fail).otherwise(remove_alb_step_03_remove_alb_from_route53)

        remove_alb_step_03_remove_alb_from_route53.next(remove_alb_step03_choice)

        remove_alb_step03_choice.when(stepfunctions.Condition.string_equals('$.remove_alb_from_route53.output.status', "ERROR"),
                    remove_alb_step_fail).otherwise(remove_alb_step_success)

        # step functions state machine
        remove_alb_state_machine = stepfunctions.StateMachine(
            self, 
            "remove-alb-statemachine",
            state_machine_name="RemoveAlbStateMachine",
            timeout=core.Duration.minutes(120),
            definition=remove_alb_step01_delete_load_balancer.next(remove_alb_step01_required_choice),
            logs=stepfunctions.LogOptions(
                destination=remove_alb_state_machine_log_group,
                level=stepfunctions.LogLevel.ALL
            )
        )

        # /**END REMOVE ALB STATE MACHINE **/

        ###################################################
        # </END> Step functions
        ###################################################


        ###################################################
        # <START> SNS Topic target lambdas
        ###################################################

        add_alb_lambda = self.get_add_alb_executor_lambda(
            add_alb_state_machine_arn=add_alb_state_machine.state_machine_arn, 
            add_alb_state_machine_name=add_alb_state_machine.state_machine_name,
            remove_alb_state_machine_arn=remove_alb_state_machine.state_machine_arn,
            remove_alb_state_machine_name=remove_alb_state_machine.state_machine_name
        )

        self.add_alb_topic.add_subscription(aws_sns_subscriptions.LambdaSubscription(add_alb_lambda))
        orchestrator_key.grant_encrypt_decrypt(add_alb_lambda)

        remove_alb_lambda = self.get_remove_alb_executor_lambda(
            add_alb_state_machine_arn=add_alb_state_machine.state_machine_arn, 
            add_alb_state_machine_name=add_alb_state_machine.state_machine_name,
            remove_alb_state_machine_arn=remove_alb_state_machine.state_machine_arn,
            remove_alb_state_machine_name=remove_alb_state_machine.state_machine_name
        )

        self.remove_alb_topic.add_subscription(aws_sns_subscriptions.LambdaSubscription(remove_alb_lambda))
        orchestrator_key.grant_encrypt_decrypt(remove_alb_lambda)

        ###################################################
        # </END> SNS Topic target lambdas
        ###################################################

        ###################################################
        # <START> Cloudwatch Scheduled Event
        ###################################################

        monitor_lambda = self.get_monitor_lambda(
            add_alb_state_machine_arn=add_alb_state_machine.state_machine_arn,
            add_alb_state_machine_name=add_alb_state_machine.state_machine_name,
            remove_alb_state_machine_arn=remove_alb_state_machine.state_machine_arn,
            remove_alb_state_machine_name=remove_alb_state_machine.state_machine_name
        )

        monitor_event_rule = events.Rule(
            self,
            "monitorEventRule",
            schedule=events.Schedule.rate(core.Duration.minutes(self.orchestrator_config['monitor']['monitorRate']))
        )

        monitor_event_rule.add_target(
            events_targets.LambdaFunction(
                monitor_lambda
                )
            )

        orchestrator_key.grant_encrypt_decrypt(monitor_lambda)

        integrity_enforcer_lambda = self.get_integrity_enforcer_lambda(
            add_alb_state_machine_arn=add_alb_state_machine.state_machine_arn,
            add_alb_state_machine_name=add_alb_state_machine.state_machine_name,
            remove_alb_state_machine_arn=remove_alb_state_machine.state_machine_arn,
            remove_alb_state_machine_name=remove_alb_state_machine.state_machine_name
        )

        integrity_enforcer_event_rule = events.Rule(
            self,
            "integrityEnforcerEventRule",
            schedule=events.Schedule.rate(core.Duration.minutes(self.orchestrator_config['monitor']['integrityEnforcerRate']))
        )

        integrity_enforcer_event_rule.add_target(
            events_targets.LambdaFunction(
                integrity_enforcer_lambda
                )
            )

        orchestrator_key.grant_encrypt_decrypt(integrity_enforcer_lambda)

        ###################################################
        # </END> Cloudwatch Scheduled Event
        ###################################################


        ###################################################
        # </END> Dynamic Resource Cleaner
        ###################################################

        # /** START DYNAMIC RESOURCE CLEANER **/

        dynamic_resource_cleaner_lambda_role = iam.Role(
            scope=self,
            id="dynamic-resource-cleaner-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )

        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_route53_read_change_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_route53_read_only_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_route53_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_waf_read_only_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_waf_associate_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_load_balancer_delete_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_load_balancer_create_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_cloudwatch_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        dynamic_resource_cleaner_lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())

        dynamic_resource_cleaner_env_vars = self.get_common_environment_variables()
        dynamic_resource_cleaner_env_vars["ADD_ALB_STATE_MACHINE_ARN"] = add_alb_state_machine.state_machine_arn
        dynamic_resource_cleaner_env_vars["ADD_ALB_STATE_MACHINE_NAME"] = add_alb_state_machine.state_machine_name
        dynamic_resource_cleaner_env_vars["REMOVE_ALB_STATE_MACHINE_ARN"] = remove_alb_state_machine.state_machine_arn
        dynamic_resource_cleaner_env_vars["REMOVE_ALB_STATE_MACHINE_NAME"] = remove_alb_state_machine.state_machine_name

        dynamic_resource_cleaner_lambda = aws_lambda.Function(
            scope=self,
            id="dynamic-resource-cleaner-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.customresources.dynamic_resource_cleanup.lambda_handler",
            role=dynamic_resource_cleaner_lambda_role,
            environment=dynamic_resource_cleaner_env_vars,
            timeout=core.Duration.minutes(15)
        )

        orchestrator_key.grant_encrypt_decrypt(dynamic_resource_cleaner_lambda)

        ###################################################
        # </END> Dynamic Resource Cleaner
        ###################################################


        ###################################################
        # <START> Cloudformation outputs
        ###################################################

        # /** END DYNAMIC RESOURCE CLEANER **/

        core.CfnOutput(
            self,
            "SnsNotificationTopicArnOutput",
            value=self.notification_topic.topic_arn,
            description="SNS Notification Topic Arn",
            export_name="SnsNotificationTopicArnOutput"
        )

        core.CfnOutput(
            self,
            "SnsAddAlbTopicArnOutput",
            value=self.add_alb_topic.topic_arn,
            description="SNS Add Alb Cloudwatch Topic Arn",
            export_name="SnsAddAlbTopicArnOutput"
        )

        core.CfnOutput(
            self,
            "SnsRemoveAlbTopicArnOutput",
            value=self.remove_alb_topic.topic_arn,
            description="SNS Remove ALb Cloudwatch Topic Arn",
            export_name="SnsRemoveAlbTopicArnOutput"
        )

        core.CfnOutput(
            self,
            "AddAlbStateMachineArnOutput",
            value=add_alb_state_machine.state_machine_arn,
            description="Add Alb State Machine Arn",
            export_name="AddAlbStateMachineArnOutput"
        )

        core.CfnOutput(
            self,
            "RemoveAlbStateMachineArnOutput",
            value=remove_alb_state_machine.state_machine_arn,
            description="Remove Alb State Machine Arn",
            export_name="RemoveAlbStateMachineArnOutput"
        )

        core.CfnOutput(
            self,
            "AddAlbStateMachineLogGroupNameOutput",
            value=add_alb_state_machine_log_group.log_group_name,
            description="Add Alb State Machine LogGroup Name",
            export_name="AddAlbStateMachineLogGroupNameOutput"
        )

        core.CfnOutput(
            self,
            "RemoveAlbStateMachineLogGroupNameOutput",
            value=remove_alb_state_machine_log_group.log_group_name,
            description="Remove Alb State Machine LogGroup Name",
            export_name="RemoveAlbStateMachineLogGroupNameOutput"
        )

        core.CfnOutput(
            self,
            "AddAlbLambdaNameOutput",
            value=add_alb_lambda.function_name,
            description="Add Alb Invoker Lambda Function Name",
            export_name="AddAlbLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "RemoveAlbLambdaNameOutput",
            value=remove_alb_lambda.function_name,
            description="Remove Alb Invoker Lambda Function Name",
            export_name="RemoveAlbLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "MonitorLambdaNameOutput",
            value=monitor_lambda.function_name,
            description="Monitor Lambda Function Name",
            export_name="MonitorLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "CreateLoadBalancerLambdaNameOutput",
            value=create_load_balancer_lambda.function_name,
            description="Create Load Balancer Lambda Function Name",
            export_name="CreateLoadBalancerLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "AssociateAlbToWafLambdaNameOutput",
            value=associate_alb_to_waf_lambda.function_name,
            description="Associate Alb to Waf Lambda Function Name",
            export_name="AssociateAlbToWafLambdaNameOutput"
        )


        core.CfnOutput(
            self,
            "AddALbToRoute53LambdaNameOutput",
            value=add_alb_to_route53_lambda.function_name,
            description="Add Alb to Route53 Lambda Function Name",
            export_name="AddALbToRoute53LambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "DeleteLoadBalancerLambdaNameOutput",
            value=delete_load_balancer_lambda.function_name,
            description="Delete Load Balancer Lambda Function Name",
            export_name="DeleteLoadBalancerLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "DisassociateAlbToWafLambdaNameOutput",
            value=disassociate_alb_to_waf_lambda.function_name,
            description="Disssociate Alb to Waf Lambda Function Name",
            export_name="DisssociateAlbToWafLambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "RemoveALbFromRoute53LambdaNameOutput",
            value=remove_alb_from_route53_lambda.function_name,
            description="Remove Alb from Route53 Lambda Function Name",
            export_name="RemoveALbFromRoute53LambdaNameOutput"
        )

        core.CfnOutput(
            self,
            "DynamicResourceCleanerLambdaNameOutput",
            value=dynamic_resource_cleaner_lambda.function_name,
            description="Dyanmic Resource Cleaner Lambda Function Name",
            export_name="DynamicResourceCleanerLambdaNameOutput"
        )
        
        ###################################################
        # </END> Cloudformatiom outputs
        ###################################################

    ##########################################################
    # <START> Define reusable lambda roles
    ##########################################################

    def get_sns_subscribe_publish_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[self.notification_topic.topic_arn],
                    actions=[
                        "sns:Publish"
                    ]
                )

    def get_cloudwatch_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents"
                    ]
                )

    def get_step_functions_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:states:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:stateMachine:RemoveAlbStateMachine",
                        f"arn:aws:states:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:stateMachine:AddAlbStateMachine"
                    ],
                    actions=[
                        "states:SendTaskSuccess",
                        "states:SendTaskFailure",
                        "states:DescribeExecution",
                        "states:GetExecutionHistory",
                        "states:StartExecution",
                        "states:SendTaskHeartbeat",
                        "states:ListExecutions"
                    ]
                )

    def get_load_balancer_readonly_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=[
                        "elasticloadbalancing:DescribeTags",
                        "elasticloadbalancing:DescribeLoadBalancers",
                        "elasticloadbalancing:DescribeTargetGroups"
                    ]
                )
    
    def get_load_balancer_create_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:loadbalancer/app/*/*",
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:targetgroup/*",
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:listener/app/*/*"
                    ],
                    actions=[
                        "elasticloadbalancing:CreateListener",
                        "elasticloadbalancing:CreateLoadBalancer",
                        "elasticloadbalancing:RegisterTargets",
                        "elasticloadbalancing:CreateTargetGroup",
                        "elasticloadbalancing:CreateRule",
                    ]
                )

    def get_load_balancer_delete_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:loadbalancer/app/*/*",
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:targetgroup/*",
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:listener/app/*/*"
                    ],
                    actions=[
                        "elasticloadbalancing:DeleteTargetGroup",
                        "elasticloadbalancing:DeleteLoadBalancer"
                    ],
                    conditions={
                        "ForAllValues:StringEquals": {
                            "aws:TagKeys": [ "FLEET_ALB_CREATION"]
                        }
                    }           
                )

    def get_waf_associate_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:loadbalancer/app/*/*",
                        f"arn:aws:wafv2:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:regional/webacl/*/*"
                    ],
                    actions=[
                        "wafv2:AssociateWebACL",
                        "wafv2:DisassociateWebACL",
                        "elasticloadbalancing:SetWebAcl",
                        "wafv2:ListResourcesForWebACL"
                    ]
                )

    
    def get_waf_read_only_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:elasticloadbalancing:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:loadbalancer/app/*/*",
                        f"arn:aws:wafv2:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:regional/webacl/*/*"
                    ],
                    actions=[
                        "wafv2:ListResourcesForWebACL"
                    ]
                )


    def get_route53_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:aws:route53:::hostedzone/{self.dns_hosted_zone.hosted_zone_id}"],
                    actions=[
                        "route53:ChangeResourceRecordSets"
                    ]
                )


    def get_route53_read_only_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:aws:route53:::hostedzone/{self.dns_hosted_zone.hosted_zone_id}"],
                    actions=[
                        "route53:ListResourceRecordSets"
                    ]
                )

    def get_route53_read_change_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["arn:aws:route53:::change/*"],
                    actions=[
                        "route53:GetChange"
                    ]
                )

    def get_ssm_parameter_execution_policy(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:aws:ssm:{core.Aws.REGION}:{core.Aws.ACCOUNT_ID}:parameter/weighted-alb-with-waf/*"],
                    actions=[
                        "ssm:GetParameter"
                    ]
                )


    def get_ecs_describe_tasks(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=[
                        "ecs:DescribeTasks",
                        "ecs:ListTasks"
                    ]
                )

    def get_ec2_read_only(self):
        return iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=[
                        "ec2:Describe*",
                        "ec2:List*",
                        "ec2:Get*",
                        "ec2:Search*",
                        "ec2:Export*"
                    ]
                )


    def get_lambda_execution_role(self, role_id):
        return iam.Role(
                    scope=self,
                    id=role_id,
                    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                    managed_policies=[
                        iam.ManagedPolicy.from_aws_managed_policy_name(
                            "service-role/AWSLambdaBasicExecutionRole"
                        )
                    ]
                )

    ##########################################################
    # </END> Define reusable lambda roles
    ##########################################################


    ##########################################################
    # <START> Define reusable lambda environment variables
    ##########################################################

    def get_common_environment_variables(self):
        return {
            "SNS_TOPIC_ARN": self.notification_topic.topic_arn,
            "SNS_TOPIC_ARN_ADD_ALB": self.add_alb_topic.topic_arn,
            "SNS_TOPIC_ARN_REMOVE_ALB": self.remove_alb_topic.topic_arn,
            "AWS_REGION_NAME": core.Aws.REGION,
            "AWS_ACCOUNT_ID": core.Aws.ACCOUNT_ID,
            "ALB_TAG_KEY": self.orchestrator_config['elbTag']['key'],
            "ALB_TAG_VALUE": self.orchestrator_config['elbTag']['value'],
            "ALB_SUBNET_IDS": ",".join([subnet.subnet_id for subnet in self.vpc.public_subnets]),
            "ALB_SECURITY_GROUPS": self.alb_sg.security_group_id,
            "ALB_VPC_ID": self.vpc.vpc_id,
            "FLEET_TAG_KEY": self.orchestrator_config['fleetTag']['key'],
            "FLEET_TAG_VALUE": self.orchestrator_config['fleetTag']['value'],
            "INTEGRITY_ENFORCER_RATE": str(self.orchestrator_config['monitor']['integrityEnforcerRate']),
            "WAF_WEB_ACL_ARN": self.web_acl.attr_arn,
            "ROUTE_53_PRIVATE_ZONE_ID": self.dns_hosted_zone.hosted_zone_id,
            "ROUTE_53_ALB_DNS_NAME": self.orchestrator_config['albRoute53DnsName'],
            "SUSPEND_REMOVE_ALB_PARAM_NAME": self.suspend_remove_alb_param.parameter_name,
            "SUSPEND_ADD_ALB_PARAM_NAME": self.suspend_add_alb_param.parameter_name
        }

    ##########################################################
    # </END> Define reusable lambda environment variables
    ##########################################################


    ##########################################################
    # <START> Define project lambdas
    ##########################################################

    # /** BEGIN STATE MACHINE EXECUTOR FUNCTIONS **/

    def get_add_alb_executor_lambda(
            self, 
            add_alb_state_machine_arn, 
            add_alb_state_machine_name,
            remove_alb_state_machine_arn, 
            remove_alb_state_machine_name
        ):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("add-alb-executor-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())
        
        env_vars = self.get_common_environment_variables()
        env_vars["ADD_ALB_STATE_MACHINE_ARN"] = add_alb_state_machine_arn
        env_vars["ADD_ALB_STATE_MACHINE_NAME"] = add_alb_state_machine_name
        env_vars["REMOVE_ALB_STATE_MACHINE_ARN"] = remove_alb_state_machine_arn
        env_vars["REMOVE_ALB_STATE_MACHINE_NAME"] = remove_alb_state_machine_name

        lambda_function = aws_lambda.Function(
            scope=self,
            id="add-alb-executor-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.executor.add_alb_executor.lambda_handler",
            role=lambda_role,
            environment=env_vars,
            timeout=core.Duration.minutes(1)
        )
        return lambda_function

    def get_remove_alb_executor_lambda(
            self, 
            add_alb_state_machine_arn, 
            add_alb_state_machine_name,
            remove_alb_state_machine_arn, 
            remove_alb_state_machine_name
        ):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("remove-alb-executor-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())

        env_vars = self.get_common_environment_variables()
        env_vars["ADD_ALB_STATE_MACHINE_ARN"] = add_alb_state_machine_arn
        env_vars["ADD_ALB_STATE_MACHINE_NAME"] = add_alb_state_machine_name
        env_vars["REMOVE_ALB_STATE_MACHINE_ARN"] = remove_alb_state_machine_arn
        env_vars["REMOVE_ALB_STATE_MACHINE_NAME"] = remove_alb_state_machine_name

        lambda_function = aws_lambda.Function(
            scope=self,
            id="remove-alb-executor-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.executor.remove_alb_executor.lambda_handler",
            role=lambda_role,
            environment=env_vars,
            timeout=core.Duration.minutes(1)
        )
        return lambda_function

    # /** END STATE MACHINE EXECUTOR FUNCTIONS **/


    # /** BEGIN MONITOR FUNCTIONS **/

    def get_monitor_lambda(
            self,
            add_alb_state_machine_arn, 
            add_alb_state_machine_name,
            remove_alb_state_machine_arn, 
            remove_alb_state_machine_name
        ):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("monitor-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())
        lambda_role.add_to_policy(self.get_waf_read_only_execution_policy())
        lambda_role.add_to_policy(self.get_waf_associate_execution_policy())
        lambda_role.add_to_policy(self.get_route53_read_only_policy())
        lambda_role.add_to_policy(self.get_route53_read_change_policy())
        lambda_role.add_to_policy(self.get_route53_execution_policy())

        env_vars = self.get_common_environment_variables()
        env_vars["ADD_ALB_STATE_MACHINE_ARN"] = add_alb_state_machine_arn
        env_vars["ADD_ALB_STATE_MACHINE_NAME"] = add_alb_state_machine_name
        env_vars["REMOVE_ALB_STATE_MACHINE_ARN"] = remove_alb_state_machine_arn
        env_vars["REMOVE_ALB_STATE_MACHINE_NAME"] = remove_alb_state_machine_name

        lambda_function = aws_lambda.Function(
            scope=self,
            id="monitor-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.monitor.monitor.lambda_handler",
            role=lambda_role,
            environment=env_vars,
            timeout=core.Duration.minutes(4)
        )
        return lambda_function


    def get_integrity_enforcer_lambda(
            self,
            add_alb_state_machine_arn, 
            add_alb_state_machine_name,
            remove_alb_state_machine_arn, 
            remove_alb_state_machine_name
        ):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("integrity-enforcer-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())
        lambda_role.add_to_policy(self.get_waf_read_only_execution_policy())
        lambda_role.add_to_policy(self.get_waf_associate_execution_policy())
        lambda_role.add_to_policy(self.get_route53_read_only_policy())
        lambda_role.add_to_policy(self.get_route53_read_change_policy())
        lambda_role.add_to_policy(self.get_route53_execution_policy())
        lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())

        env_vars = self.get_common_environment_variables()
        env_vars["ADD_ALB_STATE_MACHINE_ARN"] = add_alb_state_machine_arn
        env_vars["ADD_ALB_STATE_MACHINE_NAME"] = add_alb_state_machine_name
        env_vars["REMOVE_ALB_STATE_MACHINE_ARN"] = remove_alb_state_machine_arn
        env_vars["REMOVE_ALB_STATE_MACHINE_NAME"] = remove_alb_state_machine_name

        lambda_function = aws_lambda.Function(
            scope=self,
            id="integrity-enforcer-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.monitor.integrity_enforcer.lambda_handler",
            role=lambda_role,
            environment=env_vars,
            timeout=core.Duration.minutes(15)
        )
        return lambda_function


    # /** END MONITOR FUNCTIONS **/

   # /** BEGIN ADD ALB FUNCTIONS **/
    
    def get_create_load_balancer_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("create-loadbalancer-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())
        lambda_role.add_to_policy(self.get_load_balancer_create_execution_policy())
        lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())
        lambda_role.add_to_policy(self.get_ecs_describe_tasks())
        lambda_role.add_to_policy(self.get_ec2_read_only())

        env_vars = self.get_common_environment_variables()
        env_vars['ECS_CLUSTER_ARN'] = self.ecs_cluster.cluster_arn

        lambda_function = aws_lambda.Function(
            scope=self,
            id="create-loadbalancer-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.add_alb.create_load_balancer.lambda_handler",
            role=lambda_role,
            environment=env_vars,
            timeout=core.Duration.minutes(15)
        )
        return lambda_function

    def get_associate_alb_to_waf_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("associate-alb-to-waf-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_waf_associate_execution_policy())

        lambda_function = aws_lambda.Function(
            scope=self,
            id="associate-alb-to-waf-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.add_alb.associate_alb_to_waf.lambda_handler",
            role=lambda_role,
            environment=self.get_common_environment_variables(),
            timeout=core.Duration.minutes(2)
        )
        return lambda_function

    def get_add_alb_to_route53_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("add-alb-to-route53-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_route53_execution_policy())
        lambda_role.add_to_policy(self.get_route53_read_change_policy())
        lambda_role.add_to_policy(self.get_route53_read_only_policy())
        lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())

        lambda_function = aws_lambda.Function(
            scope=self,
            id="add-alb-to-route53-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.add_alb.add_alb_to_route_53.lambda_handler",
            role=lambda_role,
            environment=self.get_common_environment_variables(),
            timeout=core.Duration.minutes(6)
        )
        return lambda_function

    # /** END ADD ALB FUNCTIONS **/


    # /** BEGIN REMOVE ALB FUNCTIONS **/

    def get_delete_load_balancer_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("delete-loadbalancer-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_load_balancer_readonly_execution_policy())
        lambda_role.add_to_policy(self.get_load_balancer_delete_execution_policy())
        lambda_role.add_to_policy(self.get_ssm_parameter_execution_policy())

        lambda_function = aws_lambda.Function(
            scope=self,
            id="delete-loadbalancer-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.remove_alb.delete_load_balancer.lambda_handler",
            role=lambda_role,
            environment=self.get_common_environment_variables(),
            timeout=core.Duration.minutes(15)
        )
        return lambda_function

    def get_disassociate_alb_to_waf_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("disassociate-alb-to-waf-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_waf_read_only_execution_policy())

        lambda_function = aws_lambda.Function(
            scope=self,
            id="disassociate-alb-to-waf-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.remove_alb.disassociate_alb_to_waf.lambda_handler",
            role=lambda_role,
            environment=self.get_common_environment_variables(),
            timeout=core.Duration.minutes(2)
        )
        return lambda_function

    def get_remove_alb_from_route53_lambda(self):
        # IAM Role for Lambda
        lambda_role = self.get_lambda_execution_role("remove-alb-from-route53-lambda-role")
        lambda_role.add_to_policy(self.get_cloudwatch_policy())
        lambda_role.add_to_policy(self.get_sns_subscribe_publish_policy())
        lambda_role.add_to_policy(self.get_step_functions_execution_policy())
        lambda_role.add_to_policy(self.get_route53_execution_policy())
        lambda_role.add_to_policy(self.get_route53_read_change_policy())
        lambda_role.add_to_policy(self.get_route53_read_only_policy())

        lambda_function = aws_lambda.Function(
            scope=self,
            id="remove-alb-from-route53-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            code=aws_lambda.Code.from_asset(
                f"{os.path.dirname(__file__)}/resources/lambda/orchestrator",
                bundling=core.BundlingOptions(
                    image=aws_lambda.Runtime.PYTHON_3_9.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                )
            ),
            handler="app.remove_alb.remove_alb_from_route_53.lambda_handler",
            role=lambda_role,
            environment=self.get_common_environment_variables(),
            timeout=core.Duration.minutes(6)
        )
        return lambda_function

    # /** END REMOVE ALB FUNCTIONS **/

    ##########################################################
    # </END> Define project lambdas
    ##########################################################