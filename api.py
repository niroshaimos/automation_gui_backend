import os
import ast
import threading
import xml.etree.ElementTree as ET

from datetime import datetime
from datetime import date
from flask_cors import CORS
from flask import Response
from flask import Flask, request, jsonify

app = Flask(__name__)
CORS(app)
app.config['JSON_SORT_KEYS'] = False
app.config["DEBUG"] = True

XML_PATH = os.environ['XML_PATH']
RESULTS_PATH = os.environ['RESULTS_PATH']

# errors
TEST_TAG_NOT_FOUND_404 = Response("Test tag not found in tests list.", status=404, mimetype='text/plain')
INVALID_TEST_TAG_403 = Response('Invalid test tag', status=403, mimetype='text/plain')
TAG_ALREADY_IN_RUN_403 = Response('Tag is already in run.', status=403, mimetype='text/plain')
TEST_PARAMS_NOT_VALID_403 = Response('Test params are not valid.', status=403, mimetype='text/plain')
TEST_RESULT_NOT_FOUND_404 = Response('Test result not found.', status=404, mimetype='text/plain')
RUN_NOT_FOUND_404 = Response(f'Requested run not found in {RESULTS_PATH}.', status=404, mimetype='text/plain')
TEST_VARS_FILE_NOT_FOUND_400 = Response('Test vars file not found. Run might be corrupted.',
                                        status=400,
                                        mimetype='text/plain')
TEST_VARS_NOT_FOUND_400 = Response('Cannot parse vars for test. File might be corrupted.',
                                   status=400,
                                   mimetype='text/plain')
OK_202 = Response(status=202)
OK_200 = Response(status=200)


class TagNotFound(Exception):
    pass


def to_bool(val):
    if val == 'True':
        return True
    return False


def create_test(test_tag):
    test = ET.Element('test')
    test.set('tag', test_tag)
    test.set('valid', 'false')
    test.set('active', 'false')
    ET.SubElement(test, 'path')
    ET.SubElement(test, 'pn')
    ET.SubElement(test, 'sapUser')
    ET.SubElement(test, 'password')
    ET.SubElement(test, 'args')

    return test


def valid_test_tag(test_collection, new_tag):
    if ' ' in new_tag:
        return False

    if '/' in new_tag:
        return False

    if '\\' in new_tag:
        return False

    if test_collection.find(f'./test[@tag="{new_tag}"]'):
        return False

    return True


def get_test_from_collection(test_collection, test_tag):
    test = test_collection.find(f'./test[@tag="{test_tag}"]')
    if test:
        return test

    return None


def check_test_validity(test):
    for prop in test:
        if prop.tag != 'args':
            if prop.text is None or prop.text == '':
                return False

    return True


def get_test_params(test):
    test_params = {}
    for param in test:
        if param.tag != 'args':
            test_params[param.tag] = param.text if param.text else ""
        else:
            test_params[param.tag] = [{'name': arg.attrib["key"], 'value': arg.attrib["value"]} for arg in param]

    return test_params


def delete_test(root, test_tag):
    test_collection = root.find('test_collection')
    test = test_collection.find(f'./test[@tag="{test_tag}"]')
    if test is None:
        raise TagNotFound

    test_collection.remove(test)


def _deactivate_test(root, test_tag):
    test_collection = root.find('test_collection')
    test = test_collection.find(f'./test[@tag="{test_tag}"]')
    if test is None:
        return False
    test.set('active', str(False))

    return True


def get_suite_tests(test_path, suite_name, suite_test):
    tests = []
    root = ET.parse(test_path)
    index = 0
    for suite in root.findall('testsuite'):
        for test in suite:
            if test.find('failure') is not None:
                status = 'failure'
            elif test.find('skipped') is not None:
                status = 'skipped'
            elif test.find('error') is not None:
                status = 'broken'
            else:
                status = 'passed'

            tests.append({'testName': test.attrib['name'],
                          'status': status,
                          'index': str(index),
                          'suiteFile': suite_name,
                          'suiteTest': suite_test
                          })

            index += 1

    return tests


def get_tests_from_test_params(test, test_params):  # ToDo: change this name and consult the shmuck cuz this is hella shady
    tests = []
    pns = ''
    try:
        pns = ast.literal_eval(test.find('pn').text)

    except ValueError:
        pass

    if isinstance(pns, list):
        for i in range(len(pns)):
            t = test_params.copy()
            t['pn'] = pns[i]
            t['tag'] = f"{test.attrib['tag']}{i}"
            tests.append(t)

    else:
        test_params['tag'] = test.attrib['tag']
        tests.append(test_params)

    return tests


def create_run_cmd(test, result_path):
    cmd = (f'/usr/bin/python3 -m pytest -rA --junitxml={result_path} -s {test["path"]} '  # TODO
           f'--pn \'{test["pn"]}\' '
           f'--sap_user \'{test["sapUser"]}\' '
           f'--password \'{test["password"]}\' ')
    if bool(test['args']):
        args = '--args \"['
        for arg in test['args']:
            args += f'{{\'{arg["name"]}\': \'{arg["value"]}\'}}, '
        args = args[:-2]
        cmd += f'{args}]\"'

    with open(f'{result_path}_vars.txt', 'w+') as file:
        file.write(f'path:{test["path"]}\n')
        file.write(f'pn:{test["pn"]}\n')
        file.write(f'sap_user:{test["sapUser"]}\n')
        file.write(f'password:{test["password"]}\n')
        file.write(f'args:{test["args"]}\n')
    return cmd


def run_cmds(commands, path):
    for cmd in commands:
        print(cmd)
        os.system(f'{cmd} >> {path}.txt 2>&1')


def get_suite_results(suite_path):
    root = ET.parse(suite_path).getroot()
    suite_result = {
        'passed': 0,
        'errors': 0,
        'skipped': 0,
        'failures': 0
    }
    for test in root.findall('testsuite'):
        suite_result['errors'] += int(test.attrib['errors'])
        suite_result['skipped'] += int(test.attrib['skipped'])
        suite_result['failures'] += int(test.attrib['failures'])
        suite_result['passed'] += int(test.attrib['tests']) - suite_result['failures'] - suite_result['errors'] - suite_result['skipped']

    return suite_result


def append_run_results(results, new_results):
    results['errors'] += new_results['errors']
    results['skipped'] += new_results['skipped']
    results['passed'] += new_results['passed']
    results['failures'] += new_results['failures']


@app.route('/api/v1/getAllTestTags', methods=['GET'])
def get_all_tests_tags():  # find elements, also pretty useless
    root = ET.parse(XML_PATH).getroot()
    test_collection = root.find('test_collection')
    test_tags = []
    for test in test_collection:
        test_tags.append(test.attrib['tag'])

    return jsonify(test_tags)


@app.route('/api/v1/getTest/<test_tag>', methods=['GET'])
def get_test(test_tag): # change to get test params
    root = ET.parse(XML_PATH).getroot()
    test = root.find(f'./test_collection/test[@tag="{test_tag}"]')
    if test is None:
        return TEST_TAG_NOT_FOUND_404

    return jsonify(get_test_params(test=test))


@app.route('/api/v1/addTest/<test_tag>', methods=['POST'])
def add_test(test_tag):
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    test_collection = root.find('test_collection')
    if valid_test_tag(test_collection=test_collection,
                      new_tag=test_tag) is False:
        return INVALID_TEST_TAG_403

    test_collection.append(create_test(test_tag))
    tree.write(XML_PATH)

    return {'tag': test_tag, 'valid': False, 'active': False}, 202


@app.route('/api/v1/removeTest/<test_tag>', methods=['DELETE'])
def remove_test(test_tag):
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    try:
        delete_test(root=root,
                    test_tag=test_tag)
    except TagNotFound:
        return TEST_TAG_NOT_FOUND_404

    tree.write(XML_PATH)

    return {'tag': test_tag}, 202


@app.route('/api/v1/updateTest/<test_tag>', methods=['PATCH'])
def update_test(test_tag):
    test_params = request.json
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    test = root.find(f'./test_collection/test[@tag="{test_tag}"]')
    if test is None:
        return TEST_TAG_NOT_FOUND_404

    for key, value in test_params.items():
        if key != 'args':
            test.find(key).text = value
        else:
            args = ET.Element('args')
            for arg in value:
                if arg['name'] != '':
                    elem = ET.SubElement(args, 'arg')
                    elem.set('key', arg['name'])
                    elem.set('value', arg['value'])
            test.remove(test.find('args'))
            test.append(args)
    test.attrib['valid'] = str(check_test_validity(test))
    tree.write(XML_PATH)
    return {'testTag': test_tag, 'valid': to_bool(test.attrib['valid'])}, 202


@app.route('/api/v1/SetTestActiveState/<test_tag>', methods=['PATCH'])
def set_test_active_state(test_tag):  # activate test
    json = request.json

    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    test_collection = root.find('test_collection')
    test = get_test_from_collection(test_collection=test_collection,
                                    test_tag=test_tag)
    if test is None:
        return TEST_TAG_NOT_FOUND_404

    if check_test_validity(test) is False:
        return TEST_PARAMS_NOT_VALID_403

    test.attrib['active'] = str(json['active'])

    tree.write(XML_PATH)

    return {'testTag': test_tag, 'active': json['active']}, 202


@app.route('/api/v1/getAllActiveTests', methods=['GET'])
def get_all_active_tests():
    root = ET.parse(XML_PATH).getroot()
    test_collection = root.find('test_collection')
    active_tests = []
    for test in test_collection:
        active_tests.append({'tag': test.attrib['tag'], 'active': test.attrib['active']})

    return jsonify(active_tests)


@app.route('/api/v1/getAllTestStatus', methods=['GET'])  # deprecated
def get_all_tests_status():
    root = ET.parse(XML_PATH).getroot()
    test_collection = root.find('test_collection')

    return jsonify([dict(zip(['tag', 'valid'], [elem.attrib['tag'], elem.attrib['valid']]))
                    for elem in test_collection])


@app.route('/api/v1/renameTest', methods=['PATCH'])
def rename_test(old_tag, new_tag):
    root = ET.parse(XML_PATH).getroot()
    test = root.find(f'./test_collection/test[@tag="{old_tag}"]')
    if test is None:
        return TEST_TAG_NOT_FOUND_404
    test.set('tag', new_tag)

    for test in root.find('run'):
        if test.text == old_tag:
            test.text = new_tag

    response = jsonify({'testTag': new_tag})
    return response, 202


@app.route('/api/v1/getAllTests', methods=['GET'])
def get_all_tests():
    root = ET.parse(XML_PATH).getroot()
    test_collection = root.find('test_collection')

    return jsonify([dict(zip(['tag', 'valid', 'active'],
                             [elem.attrib['tag'], to_bool(elem.attrib['valid']), to_bool(elem.attrib['active'])]))
                    for elem in test_collection])


@app.route('/api/v1/runTests', methods=['PUT'])
def run_tests():
    root = ET.parse(XML_PATH).getroot()
    test_collection = root.find('test_collection')
    tests_for_run = []
    run_tag = f'{date.today().strftime("%d-%m-%Y")}_{datetime.now().strftime("%H-%M-%S")}'
    result_path = f'{RESULTS_PATH}{os.path.sep}{run_tag}'
    os.mkdir(result_path)
    # get all tests for run and their parameters
    for test in test_collection:
        if to_bool(test.attrib['active']) is True and to_bool(test.attrib['valid']) is True:
            tests = get_tests_from_test_params(test, get_test_params(test))
            if len(tests) > 1:
                tests_for_run.append({'tests': tests, 'tag': test.attrib['tag']})
            else:
                tests_for_run.extend(tests)

    run_commands = []
    # create a run command for each test
    for test in tests_for_run:
        if 'tests' in test:
            tag_result_path = f'{result_path}{os.path.sep}{test["tag"]}'
            os.mkdir(tag_result_path)
            for i in range(len(test['tests'])):
                run_commands.append(create_run_cmd(test=test['tests'][i],
                                                   result_path=f'{tag_result_path}{os.path.sep}{test["tag"]}_{i}'))

        else:
            run_commands.append(run_commands.append(create_run_cmd(test=test,
                                                                   result_path=f'{result_path}{os.path.sep}{test["tag"]}')))

    threading.Thread(target=run_cmds, args=(run_commands, f'{result_path}{os.path.sep}{run_tag}')).start()

    return OK_200


@app.route('/api/v1/getRunResults/<run_tag>')
def get_run_results(run_tag):
    run_path = f'{RESULTS_PATH}{os.path.sep}{run_tag}'
    try:
        files = os.listdir(run_path)
    except FileNotFoundError:
        return RUN_NOT_FOUND_404

    run_results = {
        'passed': 0,
        'errors': 0,
        'skipped': 0,
        'failures': 0,
    }
    for file in files:
        file_path = f'{run_path}{os.path.sep}{file}'
        if file.endswith('.txt'):
            continue

        if os.path.isdir(file_path):
            for suite_test in os.listdir(file_path):
                if not suite_test.endswith('.txt'):
                    append_run_results(results=run_results,
                                       new_results=get_suite_results(suite_path=f'{file_path}{os.path.sep}{suite_test}'))
        else:
            append_run_results(results=run_results,
                               new_results=get_suite_results(suite_path=file_path))

    run_results['total'] = sum(run_results.values())
    return jsonify(run_results)


@app.route('/api/v1/getRunTests/<run_tag>')
def get_run_tests(run_tag):
    run_path = f'{RESULTS_PATH}{os.path.sep}{run_tag}'
    try:
        files = os.listdir(run_path)
    except FileNotFoundError:
        return RUN_NOT_FOUND_404
    tests = []
    for file in files:
        if file.endswith('.txt'):
            continue

        suite_path = f'{run_path}{os.path.sep}{file}'
        if os.path.isdir(suite_path):
            for suite_test in os.listdir(suite_path):
                if not suite_test.endswith('.txt'):
                    tests.extend(get_suite_tests(test_path=f'{suite_path}{os.path.sep}{suite_test}',
                                                 suite_name=file,
                                                 suite_test=suite_test))

        else:
            tests.extend(get_suite_tests(test_path=suite_path,
                                         suite_name=file,
                                         suite_test=''))

    return jsonify(tests)


@app.route('/api/v1/getTestLog/<run_tag>/<suite_file>/<suite_test>/<index>')
def get_test_log(run_tag, suite_file, suite_test, index):
    index = int(index)  # find a better solution
    file_path = f'{RESULTS_PATH}{os.path.sep}{run_tag}{os.path.sep}{suite_file}'
    try:
        if os.path.isdir(file_path):
            root = ET.parse(f'{file_path}{os.path.sep}{suite_test}')
        else:
            root = ET.parse(file_path)
    except FileNotFoundError:
        return RUN_NOT_FOUND_404

    suites = root.findall('testsuite')
    curr_index = 0
    for suite in suites:
        testcases = suite.findall('testcase')
        if len(testcases) > index:
            test_name = testcases[index].attrib['name']
            failure = testcases[index].find('failure')
            if failure is not None:
                return jsonify({'name': test_name, 'status': 'failed', 'log': failure.text})

            skipped = testcases[index].find('skipped')
            if skipped is not None:
                return jsonify({'name': test_name, 'status': 'skipped', 'log': skipped.text})

            error = testcases[index].find('error')
            if error is not None:
                return jsonify({'name': test_name, 'status': 'broken', 'log': error.text})

            return jsonify({'name': test_name, 'status': 'passed', 'log': ''})

        curr_index += len(testcases) - 1

    return TEST_RESULT_NOT_FOUND_404


@app.route('/api/v1/getAllRuns')
def get_all_runs():
    return jsonify(os.listdir(RESULTS_PATH))


@app.route('/api/v1/getTestVariables/<run_tag>/<suite_file>/<suite_test>')
def get_test_variables(run_tag, suite_file, suite_test):
    file_path = f'{RESULTS_PATH}{os.path.sep}{run_tag}{os.path.sep}{suite_file}'

    try:
        if os.path.isdir(file_path):
            test_vars_file = f'{file_path}{os.path.sep}{suite_test}_vars.txt'
        else:
            test_vars_file = f'{file_path}_vars.txt'
    except FileNotFoundError:
        return TEST_VARS_FILE_NOT_FOUND_400

    with open(test_vars_file, 'r') as file:
        test_vars = []
        for line in file.readlines():
            var = line.split(':', 1)
            if var[0] != 'args':
                try:
                    test_vars.append({'name': var[0],
                                      'value': var[1].rstrip('\n')})
                except IndexError:
                    return TEST_VARS_NOT_FOUND_400

            else:
                test_vars.append({'name': 'args', 'value': ast.literal_eval(var[1])})

        return jsonify(test_vars)


if __name__ == '__main__':
    app.run()
