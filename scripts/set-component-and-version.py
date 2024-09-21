import subprocess
import os
import re
from collections import defaultdict

import requests
import json
import logging
logger = logging.getLogger(__name__)

JIRA_URL = "https://trend-it.atlassian.net/"

BASE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}


def create_jira_version(api_url, project_key, version_name):
    url = f"{api_url}/rest/api/3/version"

    payload = {
        "description": f"Version {version_name} created automatically",
        "name": version_name,
        "project": project_key,
        "released": True
    }

    response = requests.post(
        url,
        headers=BASE_HEADERS,
        data=json.dumps(payload)
    )

    if response.status_code != 201:
        print(f"Failed to create version. Status Code: {response.status_code}, Response: {response.text}")

    return response.status_code


def get_merge_base(default_branch):
    return subprocess.check_output(['git', 'merge-base', 'HEAD', f'remotes/origin/{default_branch}']).decode('utf-8').strip()

def get_commit_messages(merge_base):
    commits = subprocess.check_output(['git', 'log', '--oneline', f'{merge_base}..HEAD']).decode('utf-8').strip().splitlines()

    commit_messages = [line.split(' ', 1)[1] for line in commits if ' ' in line]

    task_keys = []
    pattern = r'[A-Za-z]+-[0-9]+'
    for message in commit_messages:
        matches = re.findall(pattern, message)
        task_keys.extend(matches)

    return task_keys


def get_trackers(task_keys):
    letter_parts = [task.split('-')[0] for task in task_keys]
    unique_letter_parts = set(letter_parts)
    return list(unique_letter_parts)


def get_or_create_component(app_name, tracker):
    get_components_url = f"{JIRA_URL}/rest/api/3/project/{tracker}/component"

    response = requests.request(
        "GET",
        get_components_url,
        headers=BASE_HEADERS
    )

    if response.status_code >= 400:
        raise ValueError(f"Cannot get components for {tracker}")

    components = json.loads(response.text)
    for value in components.get('values', []):
        if value.get('name') == app_name:
            return value
    payload = json.dumps( {
        "assigneeType": "PROJECT_LEAD",
        "description": "",
        "name": app_name,
        "project": tracker
    } )

    create_components_url = f"{JIRA_URL}/rest/api/3/component"
    response = requests.request("POST", create_components_url, data=payload, headers=BASE_HEADERS)
    if response.status_code >= 400:
        raise ValueError(f"Cannot create component {app_name} for {tracker}")

    return json.loads(response.text)


def get_or_create_version(app_version, tracker):
    get_versions_url = f"{JIRA_URL}/rest/api/3/project/{tracker}/version"

    response = requests.request(
        "GET",
        get_versions_url,
        headers=BASE_HEADERS
    )

    if response.status_code >= 400:
        raise ValueError(f"Cannot get versions for {tracker}")

    versions = json.loads(response.text)
    for value in versions.get('values', []):
        if value.get('name') == app_version:
            return value
    payload = json.dumps( {
        "description": f"Version {app_version} created automatically",
        "name": app_version,
        "project": tracker,
        "released": True
    })

    create_version_url = f"{JIRA_URL}/rest/api/3/version"
    response = requests.request("POST", create_version_url, data=payload, headers=BASE_HEADERS)
    if response.status_code >= 400:
        raise ValueError(f"Cannot create version {app_version} for {tracker} {response.text}")

    return json.loads(response.text)

def group_tasks_by_tracker(task_keys):
    grouped_tasks = defaultdict(list)  # Создаем словарь, где по умолчанию значение — это пустой список

    for task in task_keys:
        match = re.match(r'([A-Za-z]+)-\d+', task)
        if match:
            key = match.group(1)  # Извлекаем буквенную часть (ключ)
            grouped_tasks[key].append(task)  # Добавляем task в список для этого ключа

    return dict(grouped_tasks)


def filter_out_not_existing_issues(tasks):
    # here filter issues for update
    search_issues_url = f"{JIRA_URL}/rest/api/3/search"
    tasks_str = ", ".join(tasks)
    query = {
        'jql': f'key IN ({tasks_str})'
    }


    response = requests.request(
        "GET",
        search_issues_url,
        headers=BASE_HEADERS,
        params=query
    )

    if response.status_code >=400:
        raise ValueError(f"Cannot search issues {tasks} {response.text}")

    return list(map(lambda issue: issue['key'], json.loads(response.text)['issues']))

def update_component_and_version(tasks, component, version):
    bulk_update_url = f"{JIRA_URL}/rest/api/3/bulk/issues/fields"

    payload = json.dumps( {
        "selectedIssueIdsOrKeys": tasks,
        "sendBulkNotification": False,
        "editedFieldsInput": {
            "multiselectComponents": {
                "bulkEditMultiSelectFieldOption": "ADD",
                "components": [
                    {
                        "componentId": component['id']
                    }
                ],
                "fieldId": "components"
            },
            "multipleVersionPickerFields": [
                {
                    "bulkEditMultiSelectFieldOption": "ADD",
                    "fieldId": "fixVersions",
                    "versions": [
                        {
                            "versionId" : version['id']
                        }
                    ]
                }
            ]
        },
        "selectedActions": [
            "components",
            "fixVersions"
        ]
    })

    response = requests.request("POST", bulk_update_url, data=payload, headers=BASE_HEADERS)
    if response.status_code >= 400:
        tasks_str = ",".join(tasks)
        logger.warning(f"Cannot update issues {tasks_str} {response.text}")
        raise ValueError(f"Cannot update issues {tasks_str} {response.text}")
    logger.warning(f"task successfully submitted {response.text}")
    return json.loads(response.text)


def main():
    auth = os.getenv("JIRA_API_KEY")
    BASE_HEADERS["Authorization"] = f"Basic {auth}"
    default_branch = os.getenv("DEFAULT_BRANCH")
    merge_base = get_merge_base(default_branch)
    logger.warning(f"merge base: {merge_base}")
    task_keys = get_commit_messages(merge_base)
    logger.warning(f"raw tasks keys: {task_keys}")

    app_name = os.getenv('APP_NAME')
    app_version = os.getenv('APP_VERSION')
    logger.warning(f"version - {app_version}, component - {app_name}")

    task_keys = filter_out_not_existing_issues(task_keys)
    logger.warning(f"filtered tasks keys: {task_keys}")
    grouped_tasks = group_tasks_by_tracker(task_keys)
    for tracker in grouped_tasks.keys():
        component = get_or_create_component(app_name, tracker)
        version = get_or_create_version(app_version, tracker)
        update_component_and_version(grouped_tasks[tracker], component, version)

    ", ".join(task_keys)
    with open(os.getenv('GITHUB_OUTPUT'), 'a') as f:
        f.write(f'tasks={task_keys}\n')


def main2():
    tasks = ["TN-1", "TN-2", "BLA-3"]
    tasks = filter_out_not_existing_issues(tasks)
    grouped_tasks = group_tasks_by_tracker(tasks)
    for tracker in grouped_tasks.keys():
        component = get_or_create_component("test-13", tracker)
        version = get_or_create_version("421", tracker)
        update_component_and_version(grouped_tasks[tracker], component, version)




if __name__ == "__main__":
    main()
