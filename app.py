#!/usr/bin/env python3
import os

from aws_cdk import core
from utils.CdkUtils import CdkUtils
from stacks.weighted_alb_with_waf.weighted_alb_with_waf_stack import WeightedAlbWithWAFStack


app = core.App()

WeightedAlbWithWAFStack(
    app,
     "WeightedAlbWithWaf",
     env=core.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION'))
)

app.synth()
