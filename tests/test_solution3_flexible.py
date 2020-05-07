from os.path import dirname, abspath, join
import unittest
from unittest import TestCase
import warnings
import pprint
import logging
import sys
import json

import boto3
import botocore

# Find code directory relative to our directory
THIS_DIR = dirname(__file__)
TOP_DIR = abspath(join(THIS_DIR, ".."))
sys.path.append(TOP_DIR)

from app import env

DRY_RUN = True

log = logging.getLogger("TestPolicy")


class TestFlexibleAttributePolicy(TestCase):
    """
    These tests check whether the Principal (Role or User) used to 
    authenticate can perform these actions which makes them 
    highly context dependant.

    What are the positive conditions?
    1 - Create new Instances with 'access-project=elysian' tag
    2 - Start & Stop instances tagged 'access-project=elysian'
    3 - Decode Authorization Messages
    What are the negative conditions?
    4 - Prevent new instances without tags
    5 - Prevent new Instances with wrong tags
    6 - Prevent adding tags to existing instances without 'access-project=elysian'
    7 - Prevent starting not 'access-project=elysian'
    8 - Prevent starting instance if not all tags are included
    """

    def get_export(self, requested):
        for export in self.exports["Exports"]:
            if export["Name"] == requested:
                return export["Value"]
        raise "Unable to find export requested"

    def setUp(self):
        warnings.simplefilter("ignore", category=ResourceWarning)
        # Setting the region prevents the User profile from connecting to the wrong region.
        self.region = env["region"]
        self.session = boto3.session.Session()
        self.ec2 = self.session.resource("ec2", region_name=self.region)
        self.client = self.session.client("ec2", region_name=self.region)
        self.cfn = self.session.client("cloudformation", region_name=self.region)
        self.exports = self.cfn.list_exports()
        self.project = "elysian"
        self.team = "webdev"
        self.cost_center = "2600"

        self.ami = self.get_export("default-elysian-ami")
        self.security_group = self.get_export("test-elysian-sg")
        # Blocked instance without tag
        self.blocked_instance = self.get_export("elysian-blocked-instance")
        # Valid instance with tag: access-project=elysian
        self.valid_instance = self.get_export("elysian-valid-instance")

    # Validates simple access to Read ec2 instances.
    def test_describe_instance(self):
        """
        Principal can describe an instance.
        """
        try:
            self.client.describe_instances(
                InstanceIds=[self.valid_instance], DryRun=DRY_RUN
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "DryRunOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    # Validates simple access to Read for security groups.
    def test_get_security_group(self):
        # If this fails, it's likely all tests instance tests fail.
        security_group = self.ec2.SecurityGroup(self.security_group)
        assert security_group.group_id == self.security_group

    def test_run_instance(self):
        """
        Solves #1 positive condition
        """
        try:
            self.client.run_instances(
                ImageId=self.ami,
                InstanceType="t2.micro",
                SecurityGroupIds=[self.security_group],
                MaxCount=1,
                MinCount=1,
                Monitoring={"Enabled": False},
                TagSpecifications=[
                    {
                        "Tags": [
                            {"Key": "access-project", "Value": self.project},
                            {"Key": "access-team", "Value": self.team},
                            {"Key": "cost-center", "Value": self.cost_center},
                        ],
                        "ResourceType": "instance",
                    }
                ],
                DryRun=DRY_RUN,
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "DryRunOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_allowed_to_start_stop(self):
        """
        Solves #2 positive condition
        Because this instance does have tag:
        access-project=elysian
        It will return as "DryRunOperation"
        """
        valid_instance = self.ec2.Instance(self.valid_instance)
        try:
            valid_instance.start(DryRun=DRY_RUN)
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "DryRunOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

        try:
            valid_instance.stop(DryRun=DRY_RUN)
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "DryRunOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_decode_authorization(self):
        """
        Solves #3 positive condition
        Only required for testing.
        Does this user have "sts:DecodeAuthorizationMessage"?
        """
        sts = boto3.client("sts")
        try:
            sts.decode_authorization_message(EncodedMessage="string")
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            success = "InvalidAuthorizationMessageException" == error_message
            if success:
                assert True
            else:
                print(error_message)
                raise

    def test_run_instance_without_tags(self):
        """
        4 - Prevent new instances without tags
        """
        try:
            self.client.run_instances(
                ImageId=self.ami,
                InstanceType="t2.micro",
                SecurityGroupIds=[self.security_group],
                MaxCount=1,
                MinCount=1,
                Monitoring={"Enabled": False},
                DryRun=DRY_RUN,
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "UnauthorizedOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_run_instance_invalid_tag_name(self):
        """
        5 - Prevent new Instances with wrong tags
        """
        try:
            self.client.run_instances(
                ImageId=self.ami,
                InstanceType="t2.micro",
                SecurityGroupIds=[self.security_group],
                MaxCount=1,
                MinCount=1,
                Monitoring={"Enabled": False},
                TagSpecifications=[
                    {
                        "Tags": [{"Key": "wrong-tagname", "Value": self.project}],
                        "ResourceType": "instance",
                    }
                ],
                DryRun=DRY_RUN,
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "UnauthorizedOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_prevent_adding_tags_to_existing_instances(self):
        """
        6 - Prevent adding tags to existing instances.
        """
        blocked = self.ec2.Instance(self.blocked_instance)
        try:
            blocked.create_tags(
                DryRun=DRY_RUN, Tags=[{"Key": "access-project", "Value": self.project}],
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "UnauthorizedOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_failure_to_start(self):
        """
        Solves #7 negative condition
        Because this instance does not have tag:
        access-project=elysian
        It will return as "UnauthorizedOperation"
        """
        blocked = self.ec2.Instance(self.blocked_instance)
        try:
            blocked.start(DryRun=DRY_RUN)
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "UnauthorizedOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise

    def test_failure_of_run_instance(self):
        """
        Solves #8 negative condition
        Prevent the user from starting because only 1 tag is included by requires 3.
        It will return as "UnauthorizedOperation"
        """
        try:
            self.client.run_instances(
                ImageId=self.ami,
                InstanceType="t2.micro",
                SecurityGroupIds=[self.security_group],
                MaxCount=1,
                MinCount=1,
                Monitoring={"Enabled": False},
                TagSpecifications=[
                    {
                        "Tags": [{"Key": "access-project", "Value": self.project}],
                        "ResourceType": "instance",
                    }
                ],
                DryRun=DRY_RUN,
            )
        except botocore.exceptions.ClientError as e:
            error_message = e.response["Error"]["Code"]
            dry_run = "UnauthorizedOperation" == error_message
            if dry_run:
                assert True
            else:
                format_extracted_message(e)
                raise


def format_extracted_message(err):
    # Extract the encoded message from the error
    encoded_message = extract_encoded_message(err)
    # Decode the AWS error
    result = decode_message(encoded_message)
    # Prints out AWS error message
    pretty(json.loads(result))


def pretty(value):
    pp = pprint.PrettyPrinter(indent=4)
    return pp.pprint(value)


def extract_encoded_message(handled_exception):
    message = handled_exception.response["Error"]["Message"]
    # Find the start of 'message: ' and then add it's own length.
    trim_number = message.find("message: ") + 9
    encoded_message = message[trim_number:]
    return encoded_message


def decode_message(encoded_message):
    """
        The decoded message includes the following type of information:

        - Whether the request was denied due to an explicit deny or due to the absence of an explicit allow. For more information, see Determining Whether a Request is Allowed or Denied in the IAM User Guide .
        - The principal who made the request.
        - The requested action.
        - The requested resource.
        - The values of condition keys in the context of the user's request.
    """
    client = boto3.client("sts")
    try:
        decoded_message = client.decode_authorization_message(
            EncodedMessage=encoded_message
        )
    except botocore.exceptions.ClientError as err:
        # Catch this error? InvalidAuthorizationMessageException
        # Print a limited error message when decoding fails
        return json.dumps(err.response["Error"]["Code"])

    return decoded_message["DecodedMessage"]


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr)
    logging.getLogger("TestAttributePolicy").setLevel(logging.DEBUG)
    unittest.main()
