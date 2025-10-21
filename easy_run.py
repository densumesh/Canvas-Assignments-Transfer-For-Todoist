# -*- coding: utf-8 -*-
# Import Libraries
from typing import List
import requests
import re
import json
from todoist_api_python.api import TodoistAPI
from todoist_api_python.models import Task as tTask
from requests.auth import HTTPDigestAuth
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import time
from random import randint

# Load configuration files and creates a list of course_ids
config = {}
header = {}
param = {"per_page": "100", "include": "submission", "enrollment_state": "active"}
course_ids = []
assignments = []
todoist_tasks: List[tTask] = []
courses_id_name_dict = {}
todoist_project_dict = {}
throttle_number = 50  # Number of requests to make before sleeping for delay seconds
sleep_delay_max = 2500  # Maximum number of milliseconds to sleep for
max_added = 250  # Maximum number of assignments to add to Todoist at once. Todoist API limit is 450 requests per 15 minutes and you can quickly hit this if adding a massive number of assignments.
limit_reached = False  # Global var used to terminate early if limit is reached or API returns an error.


def main():
    print(f"  {'#'*52}")
    print(" #     Canvas-Assignments-Transfer-For-Todoist     #")
    print(f"{'#'*52}\n")
    initialize_api()
    print("API INITIALIZED")
    select_courses()
    print(f"Selected {len(course_ids)} courses")
    print("Syncing Canvas Assignments...")
    load_todoist_projects()
    load_assignments()
    load_todoist_tasks()
    create_todoist_projects()
    transfer_assignments_to_todoist()
    canvas_assignment_stats()
    print("Done!")


# Function for Yes/No response prompts during setup
def yes_no(question: str) -> bool:
    reply = None
    while reply not in ("y", "n"):
        reply = input(f"{question} (y/n): ").lower()
    return reply == "y"


# Makes sure that the user has their api keys and canvas url in the config.json
def initialize_api():
    global config
    global todoist_api

    try:
        with open("config.json") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        print("File not Found, running Initial Configuration")
        initial_config()

    # create todoist_api object globally
    todoist_api = TodoistAPI(config["todoist_api_key"].strip())
    header.update({"Authorization": f"Bearer {config['canvas_api_key'].strip()}"})


def initial_config():  # Initial configuration for first time users
    print(
        "Your Todoist API key has not been configured. To add an API token, go to your Todoist settings and copy the API token listed under the Integrations Tab. Copy the token and paste below when you are done."
    )
    config["todoist_api_key"] = input(">")
    print(
        "Your Canvas API key has not been configured. To add an API token, go to your Canvas settings and click on New Access Token under Approved Integrations. Copy the token and paste below when you are done."
    )
    config["canvas_api_key"] = input(">")
    defaults = yes_no("Use default options? (enter n for advanced config)")
    if defaults == True:
        config["canvas_api_heading"] = "https://canvas.instructure.com"
        config["todoist_task_priority"] = 1
        config["todoist_task_labels"] = []
        config["sync_null_assignments"] = True
        config["sync_locked_assignments"] = True
        config["sync_no_due_date_assignments"] = True
    if defaults == False:
        custom_url = yes_no("Use default Canvas URL? (https://canvas.instructure.com)")
        if custom_url == True:
            config["canvas_api_heading"] = "https://canvas.instructure.com"
        if custom_url == False:
            print(
                "Enter your custom Canvas URL: (example https://university.instructure.com)"
            )
            config["canvas_api_heading"] = input(">")
        advance_setup = yes_no(
            "Configure Advanced Options (change priority, labels, or sync null/locked assignments?) (enter n for default config)"
        )
        if advance_setup == True:
            print(
                "Specify the task priority (1=Priority 4, 2=Priority 3, 3=Priority 2, 4=Priority 1. (Default Priority 4)"
            )
            config["todoist_task_priority"] = int(input(">"))
            print(
                "Enter any Label names that you would like assigned to the tasks, separated by space)"
            )
            config_input = input(">")
            config["todoist_task_labels"] = config_input.split()
            null_assignments = yes_no("Sync not graded/not submittable assignments?")
            config["sync_null_assignments"] = null_assignments
            locked_assignments = yes_no("Sync locked assignments?")
            config["sync_locked_assignments"] = locked_assignments
            no_due_date_assignments = yes_no("Sync assignments with no due date?")
            config["sync_no_due_date_assignments"] = no_due_date_assignments

        else:
            config["todoist_task_priority"] = 1
            config["todoist_task_labels"] = []
            config["sync_null_assignments"] = True
            config["sync_locked_assignments"] = True
            config["sync_no_due_date_assignments"] = True
    config["courses"] = []
    with open("config.json", "w") as outfile:
        json.dump(config, outfile)


# Allows the user to select the courses that they want to transfer while generating a dictionary
# that has course ids as the keys and their names as the values


def select_courses():
    global config

    try:
        response = requests.get(
            f"{config['canvas_api_heading']}/api/v1/courses",
            headers=header,
            params=param,
        )
        if response.status_code == 401:
            print("Unauthorized; Check API Key")
            exit()
        # Note that only courses in "Active" state are returned
        if config["courses"]:
            course_ids.extend(
                list(map(lambda course_id: int(course_id), config["courses"]))
            )
            for course in response.json():
                courses_id_name_dict[course.get("id", None)] = re.sub(
                    r"[^-a-zA-Z0-9._\s]", "", course.get("name", "")
                )
            return
    except Exception as error:
        print(f"Error while loading courses: {error}")
        print(f"Check API Key and Canvas URL")
        exit()

    # If the user does not choose to use courses selected last time
    for i, course in enumerate(response.json(), start=1):
        courses_id_name_dict[course.get("id", None)] = re.sub(
            r"[^-a-zA-Z0-9._\s]", "", course.get("name", "")
        )
        if course.get("name") is not None:
            print(
                f"{str(i)} ) {courses_id_name_dict[course.get('id', '')]} : {str(course.get('id', ''))}"
            )

    print(
        "\nEnter the courses you would like to add to Todoist by entering the numbers of the items you would like to select. Separate numbers with spaces."
    )
    my_input = input(">")
    input_array = my_input.split()
    course_ids.extend(
        list(
            map(
                lambda item: response.json()[int(item) - 1].get("id", None), input_array
            )
        )
    )

    # write course ids to config.json
    config["courses"] = course_ids
    with open("config.json", "w") as outfile:
        json.dump(config, outfile)


# Iterates over the course_ids list and loads all of the users assignments
# for those classes. Appends assignment objects to assignments list
def load_assignments():
    try:
        for course_id in course_ids:
            response = requests.get(
                f"{config['canvas_api_heading']}/api/v1/courses/{str(course_id)}/assignments",
                headers=header,
                params=param,
            )
            if response.status_code == 401:
                print("Unauthorized; Check API Key")
                exit()
            paginated = response.json()
            while "next" in response.links:
                sleep()  # Throttle requests to Canvas API to prevent rate limiting on multiple pages
                response = requests.get(
                    response.links["next"]["url"], headers=header, params=param
                )
                paginated.extend(response.json())
            print(
                f"Loaded {len(paginated)} Assignments for Course {courses_id_name_dict[course_id]}"
            )
            assignments.extend(paginated)
        print(f"Loaded {len(assignments)} Total Canvas Assignments")
        return
    except Exception as error:
        print(f"Error while loading Assignments: {error}")
        print(f"Check or regenerate API Key and Canvas URL")
        exit()


# Loads all user tasks from Todoist
def load_todoist_tasks():
    pages = todoist_api.get_tasks()
    for page in pages:
        for task in page:
            todoist_tasks.append(task)
    print(f"Loaded {len(todoist_tasks)} Todoist Tasks")


# Loads all user projects from Todoist
def load_todoist_projects():
    pages = todoist_api.get_projects()
    for page in pages:
        for project in page:
            todoist_project_dict[project.name] = project.id
    print(f"Loaded {len(todoist_project_dict)} Todoist Projects")


# Checks to see if the user has a project matching their course names, if there
# is not a new project will be created
def create_todoist_projects():
    for course_id in course_ids:
        if courses_id_name_dict[course_id] not in todoist_project_dict:
            project = todoist_api.add_project(courses_id_name_dict[course_id])
            print(f"Project {courses_id_name_dict[course_id]} created")
            todoist_project_dict[project.name] = project.id
        else:
            print(f"Project {courses_id_name_dict[course_id]} exists")


# Transfers over assignments from canvas over to Todoist, the method Checks
# to make sure the assignment has not already been transferred to prevent overlap
def transfer_assignments_to_todoist():
    new_added = 0
    updated = 0
    already_synced = 0
    excluded = 0
    global limit_reached
    global throttle_number
    request_count = 0
    now_utc = datetime.now(timezone.utc)
    for assignment in assignments:
        # Only add assignments with a due date in the future
        due_at_str = assignment.get("due_at")
        due_at_dt = None
        if due_at_str is not None:
            try:
                # Parse the datetime and make it timezone-aware (Canvas dates are in UTC)
                due_at_dt = datetime.strptime(due_at_str, "%Y-%m-%dT%H:%M:%SZ")
                due_at_dt = due_at_dt.replace(tzinfo=timezone.utc)
                if due_at_dt <= now_utc:
                    # Exclude assignments with due dates in the past or now
                    continue
            except Exception as e:
                print(
                    f"Skipping assignment due to invalid due_at: {assignment.get('name')} ({due_at_str}) - {e}"
                )
                continue
        else:
            # If assignment has no due date, keep existing exclusion logic
            if config["sync_no_due_date_assignments"] == False:
                course_name = courses_id_name_dict[assignment["course_id"]]
                print(
                    f"Excluding assignment with no due date: {course_name}: {assignment['name']}"
                )
                excluded += 1
                continue

        course_name = courses_id_name_dict[assignment["course_id"]]
        project_id = todoist_project_dict[course_name]

        is_added = False
        is_synced = True

        for task in todoist_tasks:
            # Check if assignment is already added to Todoist with same name and within the same Project
            task_content = f"[{assignment['name']}]({assignment['html_url']}) Due"
            is_match = (task.project_id == project_id) and (task.content == task_content)

            if is_match:
                is_added = True
                needs_update = False
                has_description = hasattr(task, 'description') and task.description is not None and task.description != ""

                # Check if task doesn't have a description field (old tasks)
                # Always update old tasks to add description
                if not has_description:
                    needs_update = True
                    is_synced = False
                    print(
                        f"Old task found without description, will update: {course_name}:{assignment['name']}"
                    )
                # If task has a description, check if Canvas due date changed and update description
                elif has_description:
                    # Check if the Canvas due date changed compared to what's in the description
                    # Only update description if the due date information changed
                    new_description = format_task_description(due_at_dt)
                    if task.description != new_description:
                        needs_update = True
                        is_synced = False
                        print(
                            f"Canvas due date changed for: {course_name}:{assignment['name']}, updating description"
                        )

                # Update task if needed (only updates description, not the actual due date)
                if needs_update:
                    print(
                        f"Updating assignment description: {course_name}:{assignment['name']} to '{format_task_description(due_at_dt)}'"
                    )
                    update_task(assignment, task)
                    request_count += 1

                break

            # Handle case where assignment is not graded
            if config["sync_null_assignments"] == False:
                ## This is hacky, but it works for now - need to fix this
                if (
                    assignment["submission_types"][0] == "not_graded"
                    or assignment["submission_types"][0] == "none"
                    or assignment["submission_types"][0] == "on_paper"
                ):
                    print(
                        f"Excluding ungraded/non-submittable assignment: {course_name}: {assignment['name']}"
                    )
                    is_added = True
                    excluded += 1
                    break
            # Handle case where assignment is locked and unlock date is more than 2 days in the future
            if (
                assignment["unlock_at"] is not None
                and config["sync_locked_assignments"] == False
                and assignment["unlock_at"]
                > (datetime.now() + timedelta(days=3)).isoformat()
            ):
                print(
                    f"Excluding assignment that is not yet unlocked: {course_name}: {assignment['name']}: {assignment['lock_explanation']}"
                )
                is_added = True
                excluded += 1
                break
            # Handle case where assignment is locked and unlock date is empty
            if (
                assignment["locked_for_user"] == True
                and assignment["unlock_at"] is None
                and config["sync_locked_assignments"] == False
            ):
                print(
                    f"Excluding assignment that is locked: {course_name}: {assignment['name']}: {assignment['lock_explanation']}"
                )
                is_added = True
                excluded += 1
                break
        # Add assignment to Todoist if not already added - Ignore assignments that are already submitted
        if not is_added:
            if assignment["submission"]["workflow_state"] == "unsubmitted":
                print(f"Adding assignment {course_name}: {assignment['name']}")
                add_new_task(assignment, project_id)
                new_added += 1
                request_count += 1
        # Update count of updated assignments (updated due date - already updated in Todoist)
        if is_added and not is_synced:
            updated += 1
        # Update count of already synced assignments (already synced to Todoist, no updates)
        if is_synced and is_added:
            already_synced += 1
        if new_added > max_added:
            limit_reached = True
        if limit_reached:
            break
        # Throttle requests to Todoist API to prevent rate limiting, sleep every 50 requests
        if request_count % throttle_number == 0 and request_count > 1:
            print(f"Current request count: {request_count}")
            sleep()
    if limit_reached:
        print(
            f"Reached Todoist API or configured limit. Not all tasks added. Please try again in 15 minutes."
        )
    print(f"  {'-'*52}")
    print(f"Added to Todoist: {new_added}")
    print(f"Due Date Updated In Todoist: {updated}")
    print(f"Already Synced to Todoist: {already_synced}")
    print(f"Excluded: {excluded}")


# Helper function to format task description with due date
def format_task_description(due_dt=None):
    if due_dt is not None:
        # Convert UTC to Mountain Time (automatically handles MST/MDT)
        mountain_time = ZoneInfo("America/Denver")
        mt_dt = due_dt.astimezone(mountain_time)
        # Use %Z to show the actual timezone (MST or MDT)
        due_str = mt_dt.strftime("%b %d, %Y at %I:%M %p %Z")
        return f"Due: {due_str}"
    else:
        return "Due: No due date"


# Adds a new task from a Canvas assignment object to Todoist under the
# project corresponding to project_id
def add_new_task(assignment, project_id):
    global limit_reached
    try:
        due_datetime = None
        due_date = None
        due_dt = None
        if assignment["due_at"]:
            due_dt = datetime.strptime(assignment["due_at"], "%Y-%m-%dT%H:%M:%SZ")
            due_dt = due_dt.replace(tzinfo=timezone.utc)

            # If due time is 11:59pm, set as all-day (no time)
            if due_dt.hour == 6 and due_dt.minute == 59:
                due_date = due_dt.date() - timedelta(days=1)
            else:
                due_datetime = aslocaltimestr(due_dt)

        # Create task content (without due date)
        content = f"[{assignment['name']}]({assignment['html_url']}) Due"
        # Create task description (with due date)
        description = format_task_description(due_dt)

        todoist_api.add_task(
            content=content,
            description=description,
            project_id=project_id,
            due_datetime=due_datetime,
            due_date=due_date,
            labels=config["todoist_task_labels"],
            priority=4,
        )
    except Exception as error:
        print(
            f"Error while adding task: {error}, likely due to rate limiting. Try again in 15 minutes"
        )
        limit_reached = True


def canvas_assignment_stats():
    print(f"  {'-'*52}")
    print(" #     Current Canvas Assignment Statistics     #")
    print(f"Total Assignments: {len(assignments)}")
    graded_timestamps = []
    submitted = 0
    ignored_not_graded = 0
    ignored_no_submission = 0
    locked = 0
    instructor_graded = 0
    for assignment in assignments:
        # Check for assignment graded_at dates, and if graded_at is not None, add to graded_timestamps list to report most recent grade update
        if assignment["submission"]["graded_at"] is not None:
            timestamp = datetime.strptime(
                (assignment["submission"]["graded_at"]), "%Y-%m-%dT%H:%M:%SZ"
            )
            timestamp = timestamp.replace(tzinfo=timezone.utc)
            graded_timestamps.append(timestamp)
        if assignment["graded_submissions_exist"] == True:
            instructor_graded += 1
        if assignment["submission"]["workflow_state"] != "unsubmitted":
            submitted += 1
        elif assignment["locked_for_user"] == True:
            locked += 1
        elif assignment["submission_types"][0] == "none":
            ignored_no_submission += 1
        elif assignment["submission_types"][0] == "not_graded":
            ignored_not_graded += 1

    print(f"Total Submitted: {submitted}")
    print(f"Total Locked: {locked}")
    print(f"Total Unsubmittable: {ignored_no_submission}")
    print(f"Total Not_Graded: {ignored_not_graded}")
    print(
        f"Remaining (unlocked) Assignments: {(len(assignments)-submitted-ignored_not_graded-ignored_no_submission-locked)}"
    )
    print(f"\n Grading Statistics:")
    print(f"Total Currently Graded: {max(instructor_graded,len(graded_timestamps))}")
    latest_update = max(graded_timestamps, default=0)
    if latest_update == 0:
        print(f"Last Grade Update: Never")
    else:
        print(f"Last Grade Update: {aslocaltimestr(latest_update)}")


def update_task(assignment, task):
    global limit_reached
    try:
        due_dt = None
        if assignment["due_at"]:
            due_dt = datetime.strptime(assignment["due_at"], "%Y-%m-%dT%H:%M:%SZ")
            due_dt = due_dt.replace(tzinfo=timezone.utc)

        # Update ONLY the description with the new due date
        # Do NOT update due_datetime or due_date fields to allow user customization
        description = format_task_description(due_dt)

        todoist_api.update_task(
            task_id=task.id,
            description=description,
        )
    except Exception as error:
        print(f"Error while updating task: {error}")
        limit_reached = True


# Credit to https://stackoverflow.com/questions/4563272/how-to-convert-a-utc-datetime-to-a-local-datetime-using-only-standard-library
# This funciton is simply used for printing out graded and due dates in local time. It is not used for the task creation, as tasks MUST be created in UTC
def utc_to_local(utc_dt):
    # If utc_dt is already timezone-aware, convert directly
    if hasattr(utc_dt, "tzinfo") and utc_dt.tzinfo is not None:
        return utc_dt.astimezone(tz=None)
    # If it's a naive datetime, assume it's UTC and add timezone info
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz=None)


def aslocaltimestr(utc_dt):
    # Handle both datetime objects and strings
    if isinstance(utc_dt, str):
        # Parse the string first
        original_str = utc_dt
        try:
            utc_dt = datetime.strptime(original_str, "%Y-%m-%dT%H:%M:%SZ")
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            # Try without time component
            try:
                utc_dt = datetime.strptime(original_str, "%Y-%m-%d")
                utc_dt = utc_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                # If parsing fails, return the string as-is
                return original_str
    return utc_to_local(utc_dt)


# Function for throttling/sleeping
def sleep():
    delay = (
        randint(100, sleep_delay_max) / 1000
    )  # random delay for throttling/rate limiting
    print(f"Sleeping for {delay} seconds...")
    time.sleep(delay)


if __name__ == "__main__":
    main()
