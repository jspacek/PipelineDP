# Copyright 2022 OpenMined.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""DPEngine Test"""

import unittest
from unittest.mock import patch

import apache_beam as beam
import apache_beam.testing.test_pipeline as test_pipeline
import apache_beam.testing.util as beam_util
import pydp.algorithms.partition_selection as partition_selection
from absl.testing import absltest
from absl.testing import parameterized
from typing import List

import pipeline_dp
from pipeline_dp import aggregate_params as agg
from pipeline_dp.aggregate_params import SelectPartitionsParams
from pipeline_dp.budget_accounting import NaiveBudgetAccountant
from pipeline_dp.pipeline_backend import PipelineBackend
from pipeline_dp.report_generator import ReportGenerator


class DpEngineTest(parameterized.TestCase):

    def _get_default_extractors(self) -> pipeline_dp.DataExtractors:
        return pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: x,
            value_extractor=lambda x: x,
        )

    def _create_dp_engine_default(self,
                                  accountant: NaiveBudgetAccountant = None,
                                  backend: PipelineBackend = None,
                                  return_accountant: bool = False):
        if not accountant:
            accountant = NaiveBudgetAccountant(total_epsilon=1,
                                               total_delta=1e-10)
        if not backend:
            backend = pipeline_dp.LocalBackend()
        dp_engine = pipeline_dp.DPEngine(accountant, backend)
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.LAPLACE,
            metrics=[],
            max_partitions_contributed=1,
            max_contributions_per_partition=1)
        dp_engine._report_generators.append(
            ReportGenerator(aggregator_params, "test_method"))
        dp_engine._add_report_stage("DP Engine Test")
        if return_accountant:
            return dp_engine, accountant
        return dp_engine

    def _create_params_default(self):
        return (pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[agg.Metrics.COUNT, agg.Metrics.SUM, agg.Metrics.MEAN],
            min_value=0,
            max_value=1,
            max_partitions_contributed=1,
            max_contributions_per_partition=1), ["pk0", "pk10", "pk11"])

    def test_aggregate_none(self):
        with self.assertRaises(Exception):
            pipeline_dp.DPEngine(None, None).aggregate(None, None, None)

    def test_check_aggregate_params(self):
        default_extractors = self._get_default_extractors()
        default_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            max_partitions_contributed=1,
            max_contributions_per_partition=1,
            metrics=[pipeline_dp.Metrics.PRIVACY_ID_COUNT])

        test_cases = [
            {
                "desc": "None col",
                "col": None,
                "params": default_params,
                "data_extractor": default_extractors,
            },
            {
                "desc": "empty col",
                "col": [],
                "params": default_params,
                "data_extractor": default_extractors
            },
            {
                "desc": "none params",
                "col": [0],
                "params": None,
                "data_extractor": default_extractors,
            },
            {
                "desc": "None data_extractor",
                "col": [0],
                "params": default_params,
                "data_extractor": None,
            },
            {
                "desc": "data_extractor with an incorrect type",
                "col": [0],
                "params": default_params,
                "data_extractor": 1,
            },
        ]

        for test_case in test_cases:
            with self.assertRaises(Exception, msg=test_case["desc"]):
                budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                          total_delta=1e-10)
                engine = pipeline_dp.DPEngine(
                    budget_accountant=budget_accountant,
                    backend=pipeline_dp.LocalBackend())
                engine.aggregate(test_case["col"], test_case["params"],
                                 test_case["data_extractor"])

    def _check_string_contains_strings(self, string: str,
                                       substrings: List[str]):
        print(string)
        for substring in substrings:
            self.assertContainsSubsequence(string, substring)

    def test_aggregate_report(self):
        col = [[1], [2], [3], [3]]
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: f"pid{x}",
            partition_extractor=lambda x: f"pk{x}",
            value_extractor=lambda x: x)
        params1 = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            max_partitions_contributed=3,
            max_contributions_per_partition=2,
            min_value=1,
            max_value=5,
            metrics=[
                pipeline_dp.Metrics.PRIVACY_ID_COUNT, pipeline_dp.Metrics.COUNT,
                pipeline_dp.Metrics.MEAN
            ],
        )
        params2 = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            max_partitions_contributed=1,
            max_contributions_per_partition=3,
            min_value=2,
            max_value=10,
            metrics=[pipeline_dp.Metrics.SUM, pipeline_dp.Metrics.MEAN],
        )

        select_partitions_params = SelectPartitionsParams(
            max_partitions_contributed=2)

        budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                  total_delta=1e-10)
        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())
        engine.aggregate(col, params1, data_extractor)
        engine.aggregate(col, params2, data_extractor, list(range(1, 40)))
        engine.select_partitions(col, select_partitions_params, data_extractor)
        self.assertEqual(3, len(engine._report_generators))  # pylint: disable=protected-access
        budget_accountant.compute_budgets()
        self._check_string_contains_strings(
            engine._report_generators[0].report(),
            [
                "DPEngine method: aggregate",
                "metrics=['privacy_id_count', 'count', 'mean']",
                " noise_kind=gaussian", "max_value=5",
                "Partition selection: private partitions",
                "Cross-partition contribution bounding: for each privacy id randomly select max(actual_partition_contributed, 3)",
                "Private Partition selection: using Truncated Geometric method with (eps="
            ],
        )

        self._check_string_contains_strings(
            engine._report_generators[1].report(),
            [
                "metrics=['sum', 'mean']", " noise_kind=gaussian",
                "max_value=5", "Partition selection: public partitions",
                "Per-partition contribution bounding: for each privacy_id and eachpartition, randomly select max(actual_contributions_per_partition, 3)",
                "Adding empty partitions for public partitions that are missing in data"
            ],
        )

        self._check_string_contains_strings(
            engine._report_generators[2].report(),
            [
                "DPEngine method: select_partitions",
                " budget_weight=1",
                "max_partitions_contributed=2",
                "Private Partition selection: using Truncated Geometric method with",
            ],
        )

    @patch(
        'pipeline_dp.contribution_bounders.SamplingCrossAndPerPartitionContributionBounder.bound_contributions'
    )
    def test_aggregate_computation_graph_verification(self,
                                                      mock_bound_contributions):
        # Arrange
        aggregate_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[agg.Metrics.COUNT],
            max_partitions_contributed=5,
            max_contributions_per_partition=3)
        budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                  total_delta=1e-10)

        col = [[1], [2], [3], [3]]
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: f"pid{x}",
            partition_extractor=lambda x: f"pk{x}",
            value_extractor=lambda x: x)

        mock_bound_contributions.return_value = [
            [("pid1", "pk1"), (1, [1])],
            [("pid2", "pk2"), (1, [1])],
            [("pid3", "pk3"), (1, [2])],
        ]

        backend = pipeline_dp.LocalBackend()
        engine = pipeline_dp.DPEngine(budget_accountant, backend)
        engine.aggregate(col=col,
                         params=aggregate_params,
                         data_extractors=data_extractor)

        # Assert
        mock_bound_contributions.assert_called_with(unittest.mock.ANY,
                                                    aggregate_params, backend,
                                                    unittest.mock.ANY,
                                                    unittest.mock.ANY)

    @parameterized.named_parameters(
        dict(testcase_name='all_data_kept',
             min_users=1,
             strategy=pipeline_dp.PartitionSelectionStrategy.TRUNCATED_GEOMETRIC
            ),
        dict(testcase_name='1 partition left',
             min_users=5,
             strategy=pipeline_dp.PartitionSelectionStrategy.
             GAUSSIAN_THRESHOLDING),
        dict(testcase_name='empty result',
             min_users=20,
             strategy=pipeline_dp.PartitionSelectionStrategy.
             LAPLACE_THRESHOLDING),
    )
    def test_select_private_partitions_internal(
            self, min_users: int,
            strategy: pipeline_dp.PartitionSelectionStrategy):
        input = [("pk1", (3, None)), ("pk2", (10, None))]

        engine = self._create_dp_engine_default()
        expected_data_filtered = [x for x in input if x[1][0] > min_users]

        class MockPartitionStrategy(
                partition_selection.PartitionSelectionStrategy):

            def __init__(self, min_users):
                self.min_users = min_users

            def should_keep(self, num_users: int) -> bool:
                return num_users > self.min_users

        with patch(
                "pipeline_dp.partition_selection.create_partition_selection_strategy",
                return_value=MockPartitionStrategy(min_users)) as mock_method:
            max_partitions_contributed = 2
            data_filtered = engine._select_private_partitions_internal(
                input,
                max_partitions_contributed,
                max_rows_per_privacy_id=1,
                strategy=strategy)
            engine._budget_accountant.compute_budgets()
            self.assertListEqual(list(data_filtered), expected_data_filtered)
            args = list(mock_method.call_args_list)
            self.assertLen(args, 2)  # there are 2 input data.
            self.assertEqual(args[0], args[1])
            self.assertTupleEqual(
                (strategy, 1, 1e-10, max_partitions_contributed),
                tuple(args[0])[0])

    def test_aggregate_private_partition_selection_keep_everything(self):
        # Arrange
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[agg.Metrics.COUNT],
            max_partitions_contributed=1,
            max_contributions_per_partition=1)
        # Set a large budget for having the small noise and keeping all
        # partition keys.
        budget_accountant = NaiveBudgetAccountant(total_epsilon=100000,
                                                  total_delta=1e-10)

        col = list(range(10)) + list(range(100, 120))
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: f"pk{x//100}",
            value_extractor=lambda x: None)

        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())

        col = engine.aggregate(col=col,
                               params=aggregator_params,
                               data_extractors=data_extractor)
        budget_accountant.compute_budgets()

        col = list(col)

        # Assert
        approximate_expected = {"pk0": 10, "pk1": 20}
        self.assertEqual(2, len(col))  # all partition keys are kept.
        for pk, metrics_tuple in col:
            dp_count = metrics_tuple.count
            self.assertAlmostEqual(approximate_expected[pk],
                                   dp_count,
                                   delta=1e-3)

    def test_aggregate_private_partition_selection_drop_many(self):
        # Arrange
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[agg.Metrics.COUNT],
            max_partitions_contributed=1,
            max_contributions_per_partition=1)

        # Set a small budget for dropping most partition keys.
        budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                  total_delta=1e-10)

        # Input collection has 100 elements, such that each privacy id
        # contributes 1 time and each partition has 1 element.
        col = list(range(100))
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: f"pk{x}",
            value_extractor=lambda x: None)

        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())

        col = engine.aggregate(col=col,
                               params=aggregator_params,
                               data_extractors=data_extractor)
        budget_accountant.compute_budgets()

        col = list(col)

        # Assert

        # Most partition should be dropped by private partition selection.
        # This tests is non-deterministic, but it should pass with probability
        # very close to 1.
        self.assertLess(len(col), 5)

    @patch('pipeline_dp.DPEngine._create_contribution_bounder')
    @patch('pipeline_dp.DPEngine._select_private_partitions_internal')
    def test_contribution_bounds_already_enforced_computation_graph(
            self, mock_select_private_partitions_internal,
            mock_create_contribution_bounder):
        # Arrange.
        engine = self._create_dp_engine_default()
        aggregate_params, _ = self._create_params_default()
        aggregate_params.contribution_bounds_already_enforced = True
        aggregate_params.max_contributions_per_partition = 42
        data_extractors = self._get_default_extractors()
        # no privacy ids, no privacy_id_extractor
        data_extractors.privacy_id_extractor = None
        mock_select_private_partitions_internal.return_value = []

        # Act.
        engine.aggregate([1], aggregate_params, data_extractors)

        # Assert.
        mock_create_contribution_bounder.assert_not_called()
        mock_select_private_partitions_internal.assert_called_once()
        actual_max_rows_per_privacy_id = mock_select_private_partitions_internal.call_args[
            0][2]
        self.assertEqual(aggregate_params.max_contributions_per_partition,
                         actual_max_rows_per_privacy_id)

    def test_contribution_bounds_already_enforced_sensible_result(self):
        # Arrange.
        # Set large budget, so the noise is very small.
        accountant = pipeline_dp.NaiveBudgetAccountant(total_epsilon=1000,
                                                       total_delta=0.999)
        engine = self._create_dp_engine_default(accountant=accountant)
        aggregate_params, public_partitions = self._create_params_default()
        aggregate_params.contribution_bounds_already_enforced = True
        aggregate_params.metrics = [pipeline_dp.Metrics.SUM]

        input = [(pk, 1) for pk in public_partitions]

        data_extractors = pipeline_dp.DataExtractors(
            partition_extractor=lambda x: x[0], value_extractor=lambda x: x[1])
        data_extractors.privacy_id_extractor = None

        # Act.
        col = engine.aggregate(input, aggregate_params, data_extractors,
                               public_partitions)
        accountant.compute_budgets()
        col = list(col)

        # Assert.
        self.assertLen(col, len(public_partitions))
        values = [x[1].sum for x in col]
        self.assertSequenceAlmostEqual(values, [1.0] * len(public_partitions))

    def test_select_partitions(self):
        # This test is probabilistic, but the parameters were chosen to ensure
        # the test has passed at least 10000 runs.

        # Arrange
        params = SelectPartitionsParams(max_partitions_contributed=1)

        budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                  total_delta=1e-5)

        # Generate dataset as a list of (user, partition_key) tuples.
        # There partitions are generated to reflect several scenarios.

        # A partition with sufficient amount of users.
        col = [(u, "pk-many-contribs") for u in range(25)]

        # A partition with many contributions, but only a few unique users.
        col += [(100 + u // 10, "pk-many-contribs-few-users") for u in range(30)
               ]

        # A partition with few contributions.
        col += [(200 + u, "pk-few-contribs") for u in range(3)]

        # Generating 30 partitions, each with the same group of 25 users
        # 25 users is sufficient to keep the partition, but because of
        # contribution bounding, much less users per partition will be kept.
        for i in range(30):
            col += [(500 + u, f"few-contribs-after-bound{i}") for u in range(25)
                   ]

        col = list(col)
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x[0],
            partition_extractor=lambda x: x[1])

        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())

        col = engine.select_partitions(col=col,
                                       params=params,
                                       data_extractors=data_extractor)
        budget_accountant.compute_budgets()

        col = list(col)

        # Assert
        # Only one partition is retained, the one that has many unique _after_
        # applying the "max_partitions_contributed" bound is retained.
        self.assertEqual(["pk-many-contribs"], col)

    def test_check_select_partitions(self):
        """ Tests validation of parameters for select_partitions()"""
        default_extractors = self._get_default_extractors()

        test_cases = [
            {
                "desc":
                    "None col",
                "col":
                    None,
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=1,),
                "data_extractor":
                    default_extractors,
            },
            {
                "desc":
                    "empty col",
                "col": [],
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=1,),
                "data_extractor":
                    default_extractors,
            },
            {
                "desc": "none params",
                "col": [0],
                "params": None,
                "data_extractor": default_extractors,
            },
            {
                "desc":
                    "negative max_partitions_contributed",
                "col": [0],
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=-1,),
                "data_extractor":
                    default_extractors,
            },
            {
                "desc":
                    "float max_partitions_contributed",
                "col": [0],
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=1.1,),
                "data_extractor":
                    default_extractors,
            },
            {
                "desc":
                    "None data_extractor",
                "col": [0],
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=1,),
                "data_extractor":
                    None,
            },
            {
                "desc":
                    "Not a function data_extractor",
                "col": [0],
                "params":
                    pipeline_dp.SelectPartitionsParams(
                        max_partitions_contributed=1,),
                "data_extractor":
                    1,
            },
        ]

        for test_case in test_cases:
            with self.assertRaises(Exception, msg=test_case["desc"]):
                budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                          total_delta=1e-10)
                engine = pipeline_dp.DPEngine(
                    budget_accountant=budget_accountant,
                    backend=pipeline_dp.LocalBackend())
                engine.select_partitions(test_case["col"], test_case["params"],
                                         test_case["data_extractor"])

    def test_aggregate_public_partitions_drop_non_public(self):
        # Arrange
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[
                agg.Metrics.COUNT, agg.Metrics.SUM, agg.Metrics.PRIVACY_ID_COUNT
            ],
            min_value=0,
            max_value=1,
            max_partitions_contributed=1,
            max_contributions_per_partition=1)

        # Set an arbitrary budget, we are not interested in the DP outputs, only
        # the partition keys.
        budget_accountant = NaiveBudgetAccountant(total_epsilon=1,
                                                  total_delta=1e-10)

        # Input collection has 10 elements, such that each privacy id
        # contributes 1 time and each partition has 1 element.
        col = list(range(10))
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: f"pk{x}",
            value_extractor=lambda x: x)

        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())

        col = engine.aggregate(col=col,
                               params=aggregator_params,
                               data_extractors=data_extractor,
                               public_partitions=["pk0", "pk1", "pk10"])
        budget_accountant.compute_budgets()

        col = list(col)
        partition_keys = [x[0] for x in col]
        # Assert

        # Only public partitions (0, 1, 2) should be kept and the rest of the
        # partitions should be dropped.
        self.assertEqual(["pk0", "pk1", "pk10"], partition_keys)

    def test_aggregate_public_partitions_add_empty_public_partitions(self):
        # Arrange
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[
                agg.Metrics.COUNT, agg.Metrics.SUM, agg.Metrics.PRIVACY_ID_COUNT
            ],
            min_value=0,
            max_value=1,
            max_partitions_contributed=1,
            max_contributions_per_partition=1)

        # Set a high budget to add close to 0 noise.
        budget_accountant = NaiveBudgetAccountant(total_epsilon=100000,
                                                  total_delta=1 - 1e-10)

        # Input collection has 10 elements, such that each privacy id
        # contributes 1 time and each partition has 1 element.
        col = list(range(10))
        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: f"pk{x}",
            value_extractor=lambda x: 1)

        engine = pipeline_dp.DPEngine(budget_accountant=budget_accountant,
                                      backend=pipeline_dp.LocalBackend())

        col = engine.aggregate(col=col,
                               params=aggregator_params,
                               data_extractors=data_extractor,
                               public_partitions=["pk0", "pk10", "pk11"])
        budget_accountant.compute_budgets()

        col = list(col)
        partition_keys = [x[0] for x in col]
        # Assert

        # Only public partitions ("pk0") should be kept and empty public
        # partitions ("pk10", "pk11") should be added.
        self.assertEqual(["pk0", "pk10", "pk11"], partition_keys)
        self.assertAlmostEqual(1, col[0][1][0])  # "pk0" COUNT ≈ 1
        self.assertAlmostEqual(1, col[0][1][1])  # "pk0" SUM ≈ 1
        self.assertAlmostEqual(1, col[0][1][2])  # "pk0" PRIVACY_ID_COUNT ≈ 1
        self.assertAlmostEqual(0, col[1][1][0])  # "pk10" COUNT ≈ 0
        self.assertAlmostEqual(0, col[1][1][1])  # "pk10" SUM ≈ 0
        self.assertAlmostEqual(0, col[1][1][2])  # "pk10" PRIVACY_ID_COUNT ≈ 0

    def create_dp_engine_default(self,
                                 accountant: NaiveBudgetAccountant = None,
                                 backend: PipelineBackend = None):
        if not accountant:
            accountant = NaiveBudgetAccountant(total_epsilon=1,
                                               total_delta=1e-10)
        if not backend:
            backend = pipeline_dp.LocalBackend()
        dp_engine = pipeline_dp.DPEngine(accountant, backend)
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.LAPLACE,
            metrics=[],
            max_partitions_contributed=1,
            max_contributions_per_partition=1)
        dp_engine._report_generators.append(
            ReportGenerator(aggregator_params, method_name="test"))
        dp_engine._add_report_stage("DP Engine Test")
        return dp_engine

    def create_params_default(self):
        return (pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.GAUSSIAN,
            metrics=[
                agg.Metrics.COUNT, agg.Metrics.SUM, agg.Metrics.PRIVACY_ID_COUNT
            ],
            min_value=0,
            max_value=1,
            max_partitions_contributed=1,
            max_contributions_per_partition=1), ["pk0", "pk10", "pk11"])

    def run_e2e_private_partition_selection_large_budget(self, col, backend):
        # Arrange
        aggregator_params = pipeline_dp.AggregateParams(
            noise_kind=pipeline_dp.NoiseKind.LAPLACE,
            metrics=[agg.Metrics.COUNT, agg.Metrics.SUM],
            min_value=1,
            max_value=10,
            max_partitions_contributed=1,
            max_contributions_per_partition=1)

        # Set a large budget for having the small noise and keeping all
        # partition keys.
        budget_accountant = NaiveBudgetAccountant(total_epsilon=100000,
                                                  total_delta=0.99)

        data_extractor = pipeline_dp.DataExtractors(
            privacy_id_extractor=lambda x: x,
            partition_extractor=lambda x: f"pk{x//2}",
            value_extractor=lambda x: x)

        engine = pipeline_dp.DPEngine(budget_accountant, backend)

        col = engine.aggregate(col=col,
                               params=aggregator_params,
                               data_extractors=data_extractor)
        budget_accountant.compute_budgets()

        return col

    def test_run_e2e_local(self):
        input = list(range(10))

        output = self.run_e2e_private_partition_selection_large_budget(
            input, pipeline_dp.LocalBackend())

        self.assertEqual(5, len(list(output)))

    @unittest.skip("There are some problems with serialization in this test. "
                   "Tests in private_spark_test.py work normaly so probably it"
                   " is because of some missing setup.")
    def test_run_e2e_spark(self):
        import pyspark
        conf = pyspark.SparkConf()
        sc = pyspark.SparkContext.getOrCreate(conf=conf)
        input = sc.parallelize(list(range(10)))

        output = self.run_e2e_private_partition_selection_large_budget(
            input, pipeline_dp.SparkRDDBackend(sc))

        self.assertEqual(5, len(output.collect()))

    def test_run_e2e_beam(self):
        with test_pipeline.TestPipeline() as p:
            input = p | "Create input" >> beam.Create(list(range(10)))

            output = self.run_e2e_private_partition_selection_large_budget(
                input, pipeline_dp.BeamBackend())

            beam_util.assert_that(output, beam_util.is_not_empty())

    @patch(
        'pipeline_dp.combiners.create_compound_combiner_with_custom_combiners')
    @patch('pipeline_dp.combiners.create_compound_combiner')
    @patch.multiple("pipeline_dp.combiners.CustomCombiner",
                    __abstractmethods__=set())  # Mock CustomCombiner
    def test_custom_e2e_combiners(self, mock_create_standard_combiners,
                                  mock_create_custom_combiners):
        engine = self._create_dp_engine_default()

        custom_combiner = pipeline_dp.combiners.CustomCombiner()

        col = [1, 2, 3]
        params = pipeline_dp.AggregateParams(max_partitions_contributed=1,
                                             max_contributions_per_partition=1,
                                             min_value=0,
                                             max_value=1,
                                             metrics=None,
                                             custom_combiners=[custom_combiner])

        data_extractors = self._get_default_extractors()

        engine.aggregate(col, params, data_extractors)
        mock_create_custom_combiners.assert_called_once()
        mock_create_standard_combiners.assert_not_called()

    @patch('pipeline_dp.pipeline_backend.PipelineBackend.annotate')
    def test_annotate_call(self, mock_annotate_fn):
        # Arrange
        total_epsilon, total_delta = 3, 0.0001
        budget_accountant = NaiveBudgetAccountant(total_epsilon,
                                                  total_delta,
                                                  num_aggregations=3)
        dp_engine = self._create_dp_engine_default(budget_accountant)
        aggregate_params, public_partitions = self._create_params_default()
        select_partition_params = SelectPartitionsParams(2)
        extractors = self._get_default_extractors()
        input = [1, 2, 3]

        # Act and assert
        dp_engine.select_partitions(input, select_partition_params, extractors)
        dp_engine.aggregate(input, aggregate_params, extractors,
                            public_partitions)
        dp_engine.aggregate(input, aggregate_params, extractors,
                            public_partitions)
        budget_accountant.compute_budgets()

        # Assert
        self.assertEqual(3, mock_annotate_fn.call_count)
        for i_call in range(3):
            budget = mock_annotate_fn.call_args_list[i_call][1]['budget']
            self.assertEqual(total_epsilon / 3, budget.epsilon)
            self.assertEqual(total_delta / 3, budget.delta)


if __name__ == '__main__':
    absltest.main()
