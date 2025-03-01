#
# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Test storage of analysis statistics.
"""


import multiprocessing
import json
import os
import shutil
import subprocess
import unittest
import zipfile

from tempfile import TemporaryDirectory
from codechecker_api.codeCheckerDBAccess_v6.ttypes import RunFilter


from libtest import codechecker
from libtest import env


def extract(zip_file, output_dir):
    with zipfile.ZipFile(zip_file, 'r', allowZip64=True) as zipf:
        try:
            zipf.extractall(output_dir)
        except Exception:
            print("Failed to extract received ZIP.")
            import traceback
            traceback.print_exc()
            raise


class TestStorageOfAnalysisStatistics(unittest.TestCase):
    """
    This class tests the CodeChecker analysis statistics storage feature.
    """

    def setup_class(self):
        """Setup the environment for the tests then start the server."""

        # Stopping event for CodeChecker server.
        global EVENT_1
        EVENT_1 = multiprocessing.Event()

        global TEST_WORKSPACE
        TEST_WORKSPACE = env.get_workspace('storage_of_analysis_statistics')

        os.environ['TEST_WORKSPACE'] = TEST_WORKSPACE

        test_config = {}

        # Setup environment variables for the test cases.
        host_port_cfg = {'viewer_host': 'localhost',
                         'viewer_port': env.get_free_port(),
                         'viewer_product': 'storage_of_analysis_statistics'}

        test_env = env.test_env(TEST_WORKSPACE)

        codechecker_cfg = {
            'check_env': test_env,
            'workspace': TEST_WORKSPACE,
            'checkers': [],
            'viewer_product': 'storage_of_analysis_statistics',
            'reportdir': os.path.join(TEST_WORKSPACE, 'reports'),
            'analyzers': ['clangsa', 'clang-tidy']
        }

        codechecker_cfg.update(host_port_cfg)

        codechecker_cfg['run_names'] = []

        test_config['codechecker_cfg'] = codechecker_cfg

        # Export configuration for the tests.
        env.export_test_cfg(TEST_WORKSPACE, test_config)

        # Enable storage of analysis statistics and start the CodeChecker
        # server.
        env.enable_storage_of_analysis_statistics(TEST_WORKSPACE)
        print("Starting server to get results")
        server_access = codechecker.start_server(codechecker_cfg, EVENT_1)

        server_access['viewer_product'] = codechecker_cfg['viewer_product']
        codechecker.add_test_package_product(server_access, TEST_WORKSPACE)

    def teardown_class(self):
        """Stop the CodeChecker server and clean up after the tests."""

        # TODO If environment variable is set keep the workspace
        # and print out the path.
        global TEST_WORKSPACE

        check_env = env.import_test_cfg(TEST_WORKSPACE)[
            'codechecker_cfg']['check_env']
        codechecker.remove_test_package_product(TEST_WORKSPACE, check_env)

        # Let the remaining CodeChecker servers die.
        EVENT_1.set()

        print("Removing: " + TEST_WORKSPACE)
        shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)

    def setup_method(self, method):

        # Get the test workspace.
        self.test_workspace = os.environ['TEST_WORKSPACE']

        test_class = self.__class__.__name__
        print('Running ' + test_class + ' tests in ' + self.test_workspace)

        self._codechecker_cfg = env.import_codechecker_cfg(self.test_workspace)
        self._reports_dir = self._codechecker_cfg['reportdir']

        # Get the CodeChecker cmd if needed for the tests.
        self._codechecker_cmd = env.codechecker_cmd()
        self._test_dir = os.path.join(self.test_workspace, 'test_files')
        self._product_name = self._codechecker_cfg['viewer_product']
        self._analyzer_stats_dir = os.path.join(self.test_workspace,
                                                'analysis_statistics')
        try:
            os.makedirs(self._test_dir)
        except os.error:
            # Directory already exists.
            pass

        # Remove analyzer statistics directory if it exists before store.
        if os.path.exists(self._analyzer_stats_dir):
            shutil.rmtree(self._analyzer_stats_dir, ignore_errors=True)

        # Remove reports directory if it exists and create an empty one.
        if os.path.exists(self._reports_dir):
            shutil.rmtree(self._reports_dir, ignore_errors=True)

        # Setup a viewer client to test viewer API calls.
        self._cc_client = env.setup_viewer_client(self.test_workspace)
        self.assertIsNotNone(self._cc_client)

        # Change working dir to testfile dir so CodeChecker can be run easily.
        self.__old_pwd = os.getcwd()
        os.chdir(self._test_dir)

        self._source_file = os.path.join(self._test_dir, "main.cpp")

        # Init project dir.
        makefile_content = "all:\n\t$(CXX) -c main.cpp -o /dev/null\n"
        project_info_content = {
            "name": "hello",
            "clean_cmd": "",
            "build_cmd": "make"
        }

        makefile = os.path.join(self._test_dir, 'Makefile')
        with open(makefile, 'w', encoding="utf-8", errors="ignore") as make_f:
            make_f.write(makefile_content)

        project_info = os.path.join(self._test_dir, 'project_info.json')
        with open(project_info, 'w',
                  encoding="utf-8", errors="ignore") as info_f:
            json.dump(project_info_content, info_f)

        self.sources = ["""
int main()
{
  return 1 / 0; // Division by zero
}""", """
int main()
{
  return 0;
  xxx // Will cause a compilation error
}"""]

    def teardown_method(self, method):
        """Restore environment after tests have ran."""
        os.chdir(self.__old_pwd)

    def _remove_run(self, run_names):
        """ Remove runs by run names. """
        run_filter = RunFilter()
        run_filter.names = run_names
        ret = self._cc_client.removeRun(None, run_filter)
        self.assertTrue(ret)

    def _create_source_file(self, version, report_dir):
        with open(self._source_file, 'w',
                  encoding='utf-8', errors='ignore') as source_f:
            source_f.write(self.sources[version])

        build_json = os.path.join(self._test_dir, "build.json")

        # Create a compilation database.
        build_log = [
            {
                "directory": self.test_workspace,
                "command": "gcc -c " + self._source_file,
                "file": self._source_file
            },
            {
                "directory": self.test_workspace,
                "command": "clang -c " + self._source_file,
                "file": self._source_file
            }
        ]

        with open(build_json, 'w',
                  encoding="utf-8", errors="ignore") as outfile:
            json.dump(build_log, outfile)

        # Create analyze command.
        analyze_cmd = [self._codechecker_cmd, "analyze", build_json,
                       "--analyzers", "clangsa", "-o", report_dir]

        # Run analyze.
        process = subprocess.Popen(
            analyze_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._test_dir,
            encoding="utf-8",
            errors="ignore")
        process.communicate()

    def _check_analyzer_statistics_zip(self, run_name, report_dir):
        """
        Checks if compilation database file and failed zips which exists in the
        report directory can be found in the uploaded analyzer statistics zip.
        """
        product_stat_dir = os.path.join(self._analyzer_stats_dir,
                                        self._product_name)
        # Check that directory for product under analyzer statistics dir
        # has been created.
        self.assertTrue(os.path.exists(product_stat_dir))

        # Test that the product stat directory is not empty.
        self.assertTrue(os.listdir(product_stat_dir))

        zip_file = os.path.join(product_stat_dir, f"{run_name}.zip")
        with TemporaryDirectory() as zip_dir:
            extract(zip_file, zip_dir)

            # Check that analyzer files exist in the uploaded zip.
            analyzer_files = ['compile_cmd.json',
                              'compiler_info.json',
                              'metadata.json']
            for analyzer_file in analyzer_files:
                orig_file = os.path.join(report_dir, analyzer_file)
                uploaded_file = os.path.join(zip_dir, orig_file.lstrip(os.sep))
                self.assertTrue(os.path.exists(uploaded_file))

            # Check that failed zips exist in the uploaded zip.
            orig_failed_dir = os.path.join(report_dir, 'failed')
            if os.path.exists(orig_failed_dir):
                failed_dir = os.path.join(zip_dir, orig_failed_dir)
                self.assertTrue(os.path.exists(failed_dir))

    def test_storage_empty_report_dir(self):
        """
        Check that storing an empty report directory will not store analyzer
        statistics information on the server.
        """
        os.mkdir(self._reports_dir)

        # Trying to store the empty directory.
        codechecker.store(self._codechecker_cfg, 'example')

        # Check that we do not store any analyzer statistic information.
        self.assertFalse(os.path.exists(self._analyzer_stats_dir))

    def test_storage_simple_report_dir(self):
        """
        Checks storing report directory without a failure zip will not
        store any statistics.
        """
        run_name = 'example'

        self._create_source_file(0, self._reports_dir)
        codechecker.store(self._codechecker_cfg, run_name)

        product_stat_dir = os.path.join(self._analyzer_stats_dir,
                                        self._product_name)

        # Check that directory for product under analyzer statistics dir
        # does not exists
        self.assertFalse(os.path.exists(product_stat_dir))

        self._remove_run(['example'])

    def test_storage_failed_zips(self):
        """
        Checks if compilation database and failed zips can be found in the
        uploaded zip.
        """
        run_ids = None

        # Check if there is no failed files in the database yet.
        num_of_failed_files = self._cc_client.getFailedFilesCount(run_ids)
        self.assertEqual(num_of_failed_files, 0)

        failed_files = self._cc_client.getFailedFiles(run_ids)
        self.assertEqual(len(failed_files), 0)

        # Store the failure.
        self._create_source_file(1, self._reports_dir)

        codechecker.store(self._codechecker_cfg, 'statistics1')
        self._check_analyzer_statistics_zip('statistics1', self._reports_dir)

        codechecker.store(self._codechecker_cfg, 'statistics2')
        self._check_analyzer_statistics_zip('statistics2', self._reports_dir)

        # Check the failed files again in the database.
        num_of_failed_files = self._cc_client.getFailedFilesCount(run_ids)

        self.assertEqual(num_of_failed_files, 1)

        failed_files = self._cc_client.getFailedFiles(run_ids)
        self.assertEqual(len(failed_files), 1)
        self.assertTrue(self._source_file in failed_files)

        failed_file_info = failed_files[self._source_file]
        self.assertEqual(len(failed_file_info), 2)

        self.assertTrue(
            all(i.runName in ['statistics1', 'statistics2']
                for i in failed_file_info))

        self._remove_run(['statistics1', 'statistics2'])

    def test_storage_multiple_reports_dir(self):
        """
        Test storing multiple report directories.
        """
        run_ids = None
        run_name = 'multiple_report_dir'
        report_dir1 = os.path.join(self._reports_dir, 'report_dir1')
        report_dir2 = os.path.join(self._reports_dir, 'report_dir2')

        # Analyze the same project multiple times in different report
        # directories.
        self._create_source_file(1, report_dir1)
        self._create_source_file(1, report_dir2)

        cfg = self._codechecker_cfg.copy()
        cfg['reportdir'] = [report_dir1, report_dir2]
        codechecker.store(cfg, run_name)

        # Check the failed files again in the database.
        num_of_failed_files = self._cc_client.getFailedFilesCount(run_ids)
        self.assertEqual(num_of_failed_files, 1)

        self._remove_run([run_name])
