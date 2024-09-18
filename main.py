import yaml
import os
from github import Github
import argparse
from collections import defaultdict

def topological_sort(teams):
    """Perform topological sort on teams based on parent-child relationships"""
    graph = {team['name']: set() for team in teams}
    for team in teams:
        if 'parent' in team:
            graph[team['parent']].add(team['name'])

    result = []
    visited = set()

    def dfs(node):
        visited.add(node)
        for child in graph[node]:
            if child not in visited:
                dfs(child)
        result.append(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return result[::-1]

def get_dependent_teams(team_name, teams_dict):
    """Get all dependent teams (parents) for a given team"""
    dependent_teams = []
    current = team_name
    while current in teams_dict and 'parent' in teams_dict[current]:
        parent = teams_dict[current]['parent']
        dependent_teams.append(parent)
        current = parent
    return dependent_teams[::-1]

def create_or_update_team(org, team_name, maintainers, members, parent_name, dry_run=False):
    """Create or update a team in the organization with public visibility and set parent team"""
    changes = defaultdict(list)

    try:
        team = org.get_team_by_slug(team_name)
        changes['update'].append(f"Would update existing team: {team_name}")
        if team.privacy != 'closed':
            changes['update'].append(f"Would change {team_name} visibility to public")
    except:
        changes['create'].append(f"Would create new public team: {team_name}")
        team = None

    if team and not dry_run:
        if team.privacy != 'closed':
            changes['update'].append(f"Change {team_name} visibility to public")

        if parent_name:
            parent_team = org.get_team_by_slug(parent_name)
            if team.parent != parent_team:
                changes['update'].append(f"Set parent team of {team_name} to {parent_name}")

    current_members = set(member.login for member in team.get_members()) if team else set()
    desired_members = set(maintainers + members)

    for member in desired_members - current_members:
        role = "maintainer" if member in maintainers else "member"
        changes['add'].append(f"Add {member} to {team_name} as {role}")

    for member in current_members - desired_members:
        changes['remove'].append(f"Remove {member} from {team_name}")

    return changes

def apply_changes(org, team_name, maintainers, members, parent_name, changes):
    """Apply the changes to the team"""
    try:
        team = org.get_team_by_slug(team_name)
    except:
        team = org.create_team(name=team_name, privacy='closed')
        print(f"Created new public team: {team_name}")

    if team.privacy != 'closed':
        team.edit(name=team_name, privacy='closed')
        print(f"Changed {team_name} visibility to public")

    if parent_name:
        parent_team = org.get_team_by_slug(parent_name)
        if team.parent != parent_team:
            team.edit(name=team_name, parent_team_id=parent_team.id)
            print(f"Set parent team of {team_name} to {parent_name}")

    for change in changes['add']:
        member = change.split()[1]
        role = "maintainer" if member in maintainers else "member"
        user = g.get_user(member)
        team.add_membership(user, role=role)
        print(f"Added {member} to {team_name} as {role}")

    for change in changes['remove']:
        member = change.split()[1]
        user = g.get_user(member)
        team.remove_membership(user)
        print(f"Removed {member} from {team_name}")

def remove_current_user_from_teams(org, teams_dict, current_user):
    """Remove the current user from teams where they're not explicitly listed"""
    for team_name, team_data in teams_dict.items():
        if current_user not in team_data.get('maintainers', []) and current_user not in team_data.get('members', []):
            try:
                team = org.get_team_by_slug(team_name)
                if team.has_in_members(g.get_user(current_user)):
                    team.remove_membership(g.get_user(current_user))
                    print(f"Removed current user {current_user} from {team_name}")
            except:
                print(f"Failed to remove current user {current_user} from {team_name}")

def process_repository_permissions(org, repo_name, team_permissions, dry_run=False):
    """Set or update team permissions for a repository"""
    changes = defaultdict(list)

    def translate_permission(perm):
        if perm == 'read':
            return 'pull'
        elif perm == 'write':
            return 'push'
        return perm  # 'admin', 'maintain', and 'triage' remain the same

    try:
        repo = org.get_repo(repo_name)
    except:
        changes['error'].append(f"Repository {repo_name} not found")
        return changes

    current_teams = {team.name: team.permission for team in repo.get_teams()}

    for team_name, permission in team_permissions.items():
        try:
            team = org.get_team_by_slug(team_name)
            github_permission = translate_permission(permission)

            if team_name not in current_teams:
                changes['add'].append(f"Would add team {team_name} to {repo_name} with {permission} permission")
            elif current_teams[team_name] != github_permission:
                changes['update'].append(f"Would update team {team_name} permission on {repo_name} from {current_teams[team_name]} to {permission}")

            if not dry_run:
                team.update_team_repository(repo, github_permission)
                print(f"Set team {team_name} permission on {repo_name} to {permission}")
        except Exception as e:
            changes['error'].append(f"Error processing team {team_name} for {repo_name}: {str(e)}")

    for team_name in current_teams:
        if team_name not in team_permissions:
            changes['remove'].append(f"Would remove team {team_name} from {repo_name}")
            if not dry_run:
                team = org.get_team_by_slug(team_name)
                team.remove_from_repo(repo)
                print(f"Removed team {team_name} from {repo_name}")

    return changes

def print_changes(changes):
    """Print the changes that would be made"""
    for action, items in changes.items():
        if items:
            print(f"\n{action.capitalize()}:")
            for item in items:
                print(f"  {item}")

def confirm_changes():
    """Prompt user to confirm changes"""
    response = input("Do you want to proceed with these changes? (yes/no): ").lower()
    return response == 'yes' or response == 'y'

parser = argparse.ArgumentParser(description="Manage GitHub teams and repository permissions for open-telemetry organization.")
parser.add_argument('--dry-run', action='store_true', help="Perform a dry run without making actual changes")
parser.add_argument('--target', help="Specify a single team or repository to process")
parser.add_argument('--mode', choices=['teams', 'repos'], required=True, help="Specify whether to process teams or repositories")
args = parser.parse_args()

# Load GitHub token from environment variable
github_token = os.environ.get('GITHUB_TOKEN')
if not github_token:
    raise ValueError("GITHUB_TOKEN environment variable not set")

# Initialize GitHub client
g = Github(github_token)
current_user = g.get_user().login
# Get the open-telemetry organization
org = g.get_organization("open-telemetry")

# Load and parse config.yaml
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

if args.mode == 'teams':
    teams_dict = {team['name']: team for team in config['teams']}

    if args.target:
        if args.target not in teams_dict:
            print(f"Error: Team '{args.target}' not found in the configuration.")
            exit(1)
        teams_to_process = get_dependent_teams(args.target, teams_dict) + [args.target]
    else:
        teams_to_process = topological_sort(config['teams'])

    all_changes = defaultdict(list)
    for team_name in teams_to_process:
        team = teams_dict[team_name]
        changes = create_or_update_team(
            org,
            team_name,
            team.get('maintainers', []),
            team.get('members', []),
            team.get('parent'),
            dry_run=True
        )
        for action, items in changes.items():
            all_changes[action].extend(items)

        print(f"\nChanges for team {team_name}:")
        print_changes(changes)
        print("\nCurrent team structure:")
        print(f"  Maintainers: {', '.join(team.get('maintainers', []))}")
        print(f"  Members: {', '.join(team.get('members', []))}")
        print(f"  Parent: {team.get('parent', 'None')}")
        print(f"  Visibility: Public")

    if not args.dry_run and confirm_changes():
        for team_name in teams_to_process:
            team = teams_dict[team_name]
            changes = create_or_update_team(
                org,
                team_name,
                team.get('maintainers', []),
                team.get('members', []),
                team.get('parent'),
                dry_run=False
            )
        print("\nRemoving current user from teams where they're not explicitly listed...")
        remove_current_user_from_teams(org, teams_dict, current_user)
        print("\nTeam structure creation/update completed.")
    elif args.dry_run:
        print("\nDry run completed. No changes were made.")
    else:
        print("\nOperation cancelled. No changes were made.")

elif args.mode == 'repos':
    repo_dict = {repo['name']: repo for repo in config['repositories']}

    if args.target:
        if args.target not in repo_dict:
            print(f"Error: Repository '{args.target}' not found in the configuration.")
            exit(1)
        repos_to_process = [args.target]
    else:
        repos_to_process = list(repo_dict.keys())

    all_changes = defaultdict(list)
    for repo_name in repos_to_process:
        repo_data = repo_dict[repo_name]
        team_permissions = repo_data.get('teams', {})
        changes = process_repository_permissions(
            org,
            repo_name,
            team_permissions,
            dry_run=True
        )
        for action, items in changes.items():
            all_changes[action].extend(items)

        print(f"\nChanges for repository {repo_name}:")
        print_changes(changes)

    if not args.dry_run and confirm_changes():
        for repo_name in repos_to_process:
            repo_data = repo_dict[repo_name]
            team_permissions = repo_data.get('teams', {})
            changes = process_repository_permissions(
                org,
                repo_name,
                team_permissions,
                dry_run=False
            )
        print("\nRepository team permissions update completed.")
    elif args.dry_run:
        print("\nDry run completed. No changes were made.")
    else:
        print("\nOperation cancelled. No changes were made.")
