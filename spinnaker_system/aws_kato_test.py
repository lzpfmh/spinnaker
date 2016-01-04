# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Sample Usage:
#     Assuming you have created $PASSPHRASE_FILE (which you should chmod 400):
#     and $CITEST_ROOT points to the root directory of this repository
#     (which is . if you execute this from the root)
#     and $AWS_PROFILE is the name of the aws_cli profile for authenticating
#     to observe aws resources:
#
#     This first command would be used if Spinnaker itself was deployed on GCE.
#     The test needs to talk to GCE to get to spinnaker (using the gce_* params)
#     then talk to AWS (using the aws_profile with the aws cli program) to
#     verify Spinnaker had the right effects on AWS.
#
#     PYTHONPATH=$CITEST_ROOT:$CITEST_ROOT/spinnaker \
#       python $CITEST_ROOT/spinnaker/spinnaker_system/aws_kato_test.py \
#       --gce_ssh_passphrase_file=$PASSPHRASE_FILE \
#       --gce_project=$PROJECT \
#       --gce_zone=$GCE_ZONE \
#       --gce_instance=$INSTANCE \
#       --test_aws_zone=$AWS_ZONE \
#       --aws_profile=$AWS_PROFILE
#
#   or
#
#     This second command would be used if Spinnaker itself was deployed some
#     place reachable through a direct IP connection. It could be, but is not
#     necessarily deployed on GCE. It is similar to above except it does not
#     need to go through GCE and its firewalls to locate the actual IP endpoints
#     rather those are already known and accessible.
#
#     PYTHONPATH=$CITEST_ROOT:$CITEST_ROOT/spinnaker \
#       python $CITEST_ROOT/spinnaker/spinnaker_system/aws_kato_test.py \
#       --native_hostname=host-running-kato
#       --test_aws_zone=$AWS_ZONE \
#       --aws_profile=$AWS_PROFILE
#
#   Note that the $AWS_ZONE is not directly used, rather it is a standard
#   parameter being used to infer the region. The test is going to pick
#   some different availability zones within the region in order to test kato.
#   These are currently hardcoded in.

import sys

import citest.aws_testing as aws
import citest.gcp_testing as gcp
import citest.json_contract as jc
import citest.service_testing as st

import spinnaker_testing as sk
import spinnaker_testing.kato as kato


class AwsKatoTestScenario(sk.SpinnakerTestScenario):
  @classmethod
  def new_agent(cls, bindings):
    return kato.new_agent(bindings)

  def __init__(self, bindings, agent=None):
    super(AwsKatoTestScenario, self).__init__(bindings, agent)
    self.TEST_AWS_REGION = self.agent.deployed_config.get(
      'providers.aws.defaultRegion', 'us-east-1')

  def upsert_load_balancer(self):
    detail_raw_name = 'katotestlb' + self.test_id
    self._use_lb_name = detail_raw_name

    bindings = self.bindings
    region = self.TEST_AWS_REGION
    avail_zones = [region + 'a', region + 'b']

    listener = {
      'Listener': {
         'InstancePort':7001,
         'LoadBalancerPort':80
      }
    }
    health_check = {
      'HealthyThreshold':8,
      'UnhealthyThreshold':3,
      'Interval':123,
      'Timeout':12,
      'Target':'HTTP:%d/healthcheck' % listener['Listener']['InstancePort']
    }
    path = 'healthcheck'

    payload = self.agent.type_to_payload(
      'upsertAmazonLoadBalancerDescription',
      {
        'credentials': bindings['AWS_CREDENTIALS'],
        'clusterName': bindings['TEST_APP'],
        'name': detail_raw_name,
        'availabilityZones': { self.TEST_AWS_REGION: avail_zones },
        'listeners': [{
                'internalProtocol': 'HTTP',
                'internalPort': listener['Listener']['InstancePort'],
                'externalProtocol': 'HTTP',
                'externalPort': listener['Listener']['LoadBalancerPort']
          }],
        'healthCheck': health_check['Target'],
        'healthTimeout': health_check['Timeout'],
        'healthInterval': health_check['Interval'],
        'healthyThreshold': health_check['HealthyThreshold'],
        'unhealthyThreshold': health_check['UnhealthyThreshold']
       })

    builder = aws.AwsContractBuilder(self.aws_observer)
    (builder.new_clause_builder('Load Balancer Added', retryable_for_secs=30)
       .collect_resources(
           aws_module='elb',
           command='describe-load-balancers',
           args=['--load-balancer-names', self._use_lb_name])
       .contains_group(
           [jc.PathContainsPredicate(
               'LoadBalancerDescriptions/HealthCheck', health_check),
            jc.PathEqPredicate(
               'LoadBalancerDescriptions/AvailabilityZones', avail_zones),
            jc.PathElementsContainPredicate(
               'LoadBalancerDescriptions/ListenerDescriptions', listener)]))

    return st.OperationContract(
        self.new_post_operation(
            title='upsert_amazon_load_balancer', data=payload, path='ops'),
        contract=builder.build())


  def delete_load_balancer(self):
    payload = self.agent.type_to_payload(
          'deleteAmazonLoadBalancerDescription',
          {
            'credentials': self.bindings['AWS_CREDENTIALS'],
            'regions': [self.TEST_AWS_REGION],
            'loadBalancerName': self._use_lb_name
          })

    builder = aws.AwsContractBuilder(self.aws_observer)
    (builder.new_clause_builder('Load Balancer Removed')
        .collect_resources(
            aws_module='elb',
            command='describe-load-balancers',
            args=['--load-balancer-names', self._use_lb_name],
            no_resources_ok=True)
        .excludes('LoadBalancerName', self._use_lb_name))

    return st.OperationContract(
      self.new_post_operation(
          title='delete_amazon_load_balancer', data=payload, path='ops'),
      contract=builder.build())


class AwsKatoIntegrationTest(st.AgentTestCase):
  def test_a_upsert_load_balancer(self):
    self.run_test_case(self.scenario.upsert_load_balancer())

  def test_z_delete_load_balancer(self):
    self.run_test_case(self.scenario.delete_load_balancer())


def main():
  defaults = {
    'TEST_APP': 'awskatotest' + AwsKatoTestScenario.DEFAULT_TEST_ID
  }

  return st.ScenarioTestRunner.main(
      AwsKatoTestScenario,
      default_binding_overrides=defaults,
      test_case_list=[AwsKatoIntegrationTest])


if __name__ == '__main__':
  sys.exit(main())