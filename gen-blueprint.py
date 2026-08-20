#!/usr/bin/env python3
"""Generate an authentik blueprint containing a synthetic company directory.

Blueprint schema: https://goauthentik.io/blueprints/schema.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from random import Random

import yaml
from faker import Faker

EMAIL_DOMAIN = "authentik.company"

# Groups every employee belongs to, regardless of department.
BASELINE_ACCESS = ["Google Workspace Users", "Slack Users", "Zoom Users", "VPN Users"]

# (group, parent) pairs, parents first. Nesting mirrors how access tiers stack:
# a member of "AWS Administrators" is also a member of "Cloud Platform Access".
ACCESS_GROUPS = [
    ("Application Access", None),
    ("Google Workspace Users", "Application Access"),
    ("Slack Users", "Application Access"),
    ("Zoom Users", "Application Access"),
    ("Atlassian Users", "Application Access"),
    ("Jira Users", "Atlassian Users"),
    ("Confluence Users", "Atlassian Users"),
    ("GitHub Users", "Application Access"),
    ("GitHub Administrators", "GitHub Users"),
    ("Salesforce Users", "Application Access"),
    ("Salesforce Administrators", "Salesforce Users"),
    ("Zendesk Agents", "Application Access"),
    ("Workday Users", "Application Access"),
    ("NetSuite Users", "Application Access"),
    ("Figma Editors", "Application Access"),
    ("Grafana Viewers", "Application Access"),
    ("Grafana Editors", "Grafana Viewers"),
    ("Infrastructure Access", None),
    ("VPN Users", "Infrastructure Access"),
    ("VPN Users (Privileged)", "VPN Users"),
    ("Cloud Platform Access", "Infrastructure Access"),
    ("AWS Read Only", "Cloud Platform Access"),
    ("AWS Power Users", "AWS Read Only"),
    ("AWS Administrators", "AWS Power Users"),
    ("Kubernetes Cluster Access", "Infrastructure Access"),
    ("Kubernetes Cluster Admins", "Kubernetes Cluster Access"),
    ("Production Database Read Only", "Infrastructure Access"),
]

EXTRA_ACCESS = ["Figma Editors", "Grafana Editors", "Grafana Viewers", "Confluence Users", "Jira Users"]

OFFICES = [
    ("San Francisco, CA", 22),
    ("New York, NY", 14),
    ("Austin, TX", 10),
    ("London, UK", 12),
    ("Berlin, DE", 10),
    ("Remote (US)", 20),
    ("Remote (EMEA)", 12),
]

DEPARTMENTS = {
    "Engineering": {
        "weight": 30,
        "cost_center": "CC-4000",
        "teams": [
            "Platform Engineering",
            "Frontend Engineering",
            "Backend Engineering",
            "Site Reliability Engineering",
            "Security Engineering",
            "Quality Engineering",
        ],
        "titles": [
            "Software Engineer",
            "Software Engineer II",
            "Senior Software Engineer",
            "Staff Software Engineer",
            "Principal Engineer",
        ],
        "access": ["GitHub Users", "Jira Users", "Confluence Users", "Grafana Viewers", "AWS Read Only"],
        "team_access": {
            "Platform Engineering": ["GitHub Administrators", "Kubernetes Cluster Access"],
            "Site Reliability Engineering": [
                "AWS Power Users",
                "Kubernetes Cluster Admins",
                "Production Database Read Only",
            ],
            "Security Engineering": ["VPN Users (Privileged)", "Production Database Read Only"],
        },
    },
    "Product": {
        "weight": 8,
        "cost_center": "CC-4100",
        "teams": ["Product Management", "Product Design", "User Research"],
        "titles": ["Product Manager", "Senior Product Manager", "Product Designer", "UX Researcher"],
        "access": ["Jira Users", "Confluence Users", "Figma Editors", "Grafana Viewers"],
        "team_access": {},
    },
    "Sales": {
        "weight": 16,
        "cost_center": "CC-2000",
        "teams": ["Enterprise Sales", "Commercial Sales", "Sales Engineering", "Sales Operations"],
        "titles": [
            "Account Executive",
            "Senior Account Executive",
            "Sales Development Representative",
            "Solutions Engineer",
        ],
        "access": ["Salesforce Users", "NetSuite Users"],
        "team_access": {
            "Sales Operations": ["Salesforce Administrators"],
            "Sales Engineering": ["GitHub Users", "Grafana Viewers"],
        },
    },
    "Marketing": {
        "weight": 8,
        "cost_center": "CC-2100",
        "teams": ["Demand Generation", "Product Marketing", "Brand & Communications"],
        "titles": ["Marketing Manager", "Content Strategist", "Growth Marketer", "Events Manager"],
        "access": ["Salesforce Users", "Figma Editors"],
        "team_access": {},
    },
    "Customer Success": {
        "weight": 12,
        "cost_center": "CC-3000",
        "teams": ["Technical Support", "Onboarding & Implementation", "Renewals"],
        "titles": [
            "Support Engineer",
            "Senior Support Engineer",
            "Customer Success Manager",
            "Implementation Consultant",
        ],
        "access": ["Zendesk Agents", "Salesforce Users", "Jira Users"],
        "team_access": {"Technical Support": ["Grafana Viewers"]},
    },
    "Finance": {
        "weight": 6,
        "cost_center": "CC-1000",
        "teams": ["Accounting", "Financial Planning & Analysis", "Payroll"],
        "titles": ["Accountant", "Financial Analyst", "Controller", "Payroll Specialist"],
        "access": ["NetSuite Users", "Workday Users"],
        "team_access": {},
    },
    "People": {
        "weight": 5,
        "cost_center": "CC-1100",
        "teams": ["Talent Acquisition", "People Operations", "Learning & Development"],
        "titles": ["Recruiter", "People Partner", "HR Generalist", "Enablement Manager"],
        "access": ["Workday Users"],
        "team_access": {},
    },
    "Legal": {
        "weight": 3,
        "cost_center": "CC-1200",
        "teams": ["Corporate Legal", "Compliance & Privacy"],
        "titles": ["Corporate Counsel", "Paralegal", "Compliance Manager", "Privacy Analyst"],
        "access": ["Workday Users", "Confluence Users"],
        "team_access": {},
    },
    "IT": {
        "weight": 7,
        "cost_center": "CC-5000",
        "teams": ["IT Helpdesk", "Identity & Access Management", "Corporate Infrastructure"],
        "titles": ["IT Specialist", "Systems Administrator", "IAM Engineer", "Endpoint Engineer"],
        "access": ["Jira Users", "Workday Users", "AWS Read Only", "VPN Users (Privileged)"],
        "team_access": {
            "Identity & Access Management": ["authentik Administrators", "AWS Administrators"],
            "Corporate Infrastructure": ["AWS Power Users"],
        },
    },
    "Operations": {
        "weight": 5,
        "cost_center": "CC-1300",
        "teams": ["Business Operations", "Facilities", "Procurement"],
        "titles": ["Operations Manager", "Facilities Coordinator", "Procurement Specialist", "Business Analyst"],
        "access": ["NetSuite Users", "Confluence Users"],
        "team_access": {},
    },
}


class KeyOf(str):
    """Group reference, dumped as authentik's `!KeyOf` YAML tag."""


class BlueprintDumper(yaml.SafeDumper):
    def choose_scalar_style(self):
        # PyYAML quotes any tagged scalar; blueprints conventionally write `!KeyOf some-id` plain.
        if self.event.tag == "!KeyOf":
            return ""
        return super().choose_scalar_style()


BlueprintDumper.add_representer(KeyOf, lambda dumper, data: dumper.represent_scalar("!KeyOf", str(data)))


def slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


class DirectoryGenerator:
    def __init__(self, user_count: int, seed: int):
        self.user_count = user_count
        self.random = Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.entries: list[dict] = []
        self.usernames: set[str] = set()
        self.credentials: list[dict] = []
        self.employee_number = 10000
        self.dept_groups: dict[str, str] = {}
        self.team_groups: dict[str, dict[str, str]] = {}
        self.office_groups: list[str] = []

    def generate(self) -> dict:
        self._add_groups()
        self._add_users()
        return {
            "version": 1,
            "metadata": {
                "name": f"Synthetic company directory ({self.user_count} users)",
                "labels": {
                    "blueprints.goauthentik.io/description": (
                        "Generated users, groups and nested group memberships for benchmarking."
                    ),
                },
            },
            "entries": self.entries,
        }

    def _group(self, name: str, parents: list[str] = None, attributes: dict = None, is_superuser: bool = False) -> str:
        group_id = f"group-{slug(name)}"
        attrs = {}
        if parents:
            attrs["parents"] = [KeyOf(parent) for parent in parents]
        if is_superuser:
            attrs["is_superuser"] = True
        if attributes:
            attrs["attributes"] = attributes
        entry = {"model": "authentik_core.group", "id": group_id, "identifiers": {"name": name}}
        if attrs:
            entry["attrs"] = attrs
        self.entries.append(entry)
        return group_id

    def _add_groups(self):
        self.all_employees = self._group("All Employees", attributes={"kind": "company"})
        self.managers = self._group("Managers", [self.all_employees], {"kind": "role"})
        self.leadership = self._group("Leadership", [self.managers], {"kind": "role"})
        self.contractors = self._group("Contractors", attributes={"kind": "employment-type"})

        for name, dept in DEPARTMENTS.items():
            self.dept_groups[name] = self._group(
                name,
                [self.all_employees],
                {"kind": "department", "cost_center": dept["cost_center"]},
            )
        for name, dept in DEPARTMENTS.items():
            self.team_groups[name] = {
                team: self._group(team, [self.dept_groups[name]], {"kind": "team", "department": name})
                for team in dept["teams"]
            }

        offices_root = self._group("Offices", attributes={"kind": "location"})
        for office, _ in OFFICES:
            self.office_groups.append(self._group(office, [offices_root], {"kind": "office", "city": office}))

        for name, parent in ACCESS_GROUPS:
            parents = [f"group-{slug(parent)}"] if parent else None
            self._group(name, parents, {"kind": "access"})
        self._group("authentik Administrators", attributes={"kind": "access"}, is_superuser=True)

    def _department_sizes(self) -> dict[str, int]:
        total_weight = sum(dept["weight"] for dept in DEPARTMENTS.values())
        sizes = {
            name: self.user_count * dept["weight"] // total_weight for name, dept in DEPARTMENTS.items()
        }
        # Hand the rounding remainder to the largest departments so the total is exact.
        largest = sorted(DEPARTMENTS, key=lambda name: -DEPARTMENTS[name]["weight"])
        for index in range(self.user_count - sum(sizes.values())):
            sizes[largest[index % len(largest)]] += 1
        return sizes

    def _unique_username(self, first: str, last: str) -> str:
        base = re.sub(r"[^a-z]", "", f"{first[0]}{last}".lower())
        username = base
        suffix = 2
        while username in self.usernames:
            username = f"{base}{suffix}"
            suffix += 1
        self.usernames.add(username)
        return username

    def _access_groups(self, dept_name: str, team_name: str | None) -> list[str]:
        dept = DEPARTMENTS[dept_name]
        names = BASELINE_ACCESS + dept["access"] + dept["team_access"].get(team_name, [])
        names += self.random.sample(EXTRA_ACCESS, self.random.choices([0, 1, 2], [70, 22, 8])[0])
        return [f"group-{slug(name)}" for name in names]

    def _add_users(self):
        for dept_name, size in self._department_sizes().items():
            if not size:
                continue
            dept = DEPARTMENTS[dept_name]
            director = self._add_user(
                dept_name, None, f"Director, {dept_name}", [self.managers, self.leadership]
            )
            leads: dict[str, str] = {}
            for index in range(size - 1):
                team = dept["teams"][index % len(dept["teams"])]
                if team not in leads:
                    leads[team] = self._add_user(
                        dept_name, team, f"Manager, {team}", [self.managers], manager=director
                    )
                    continue
                self._add_user(
                    dept_name,
                    team,
                    self.random.choice(dept["titles"]),
                    manager=leads[team],
                )

    def _add_user(
        self,
        dept_name: str,
        team_name: str | None,
        title: str,
        extra_groups: list[str] = (),
        manager: str = None,
    ) -> str:
        first, last = self.fake.first_name(), self.fake.last_name()
        username = self._unique_username(first, last)
        email = f"{username}@{EMAIL_DOMAIN}"
        password = self.fake.password(length=24, special_chars=True, digits=True, upper_case=True)
        self.employee_number += 1
        self.credentials.append({"username": username, "email": email, "password": password})

        # Only rank-and-file members can be contractors.
        contractor = not extra_groups and self.random.random() < 0.06
        home = self.team_groups[dept_name][team_name] if team_name else self.dept_groups[dept_name]
        groups = [home, self.random.choices(self.office_groups, [weight for _, weight in OFFICES])[0]]
        groups += self._access_groups(dept_name, team_name)
        groups += list(extra_groups)
        if contractor:
            groups.append(self.contractors)

        attributes = {
            "title": title,
            "department": dept_name,
            "employee_number": f"{'C' if contractor else 'E'}{self.employee_number}",
            "cost_center": DEPARTMENTS[dept_name]["cost_center"],
        }
        if team_name:
            attributes["team"] = team_name
        if manager:
            attributes["manager"] = manager

        self.entries.append(
            {
                "model": "authentik_core.user",
                "id": f"user-{username}",
                "identifiers": {"username": username},
                "attrs": {
                    "name": f"{first} {last}",
                    "email": email,
                    "password": password,
                    "is_active": True,
                    "type": "external" if contractor else "internal",
                    "path": f"users/{slug(dept_name)}",
                    "attributes": attributes,
                    "groups": [KeyOf(group) for group in dict.fromkeys(groups)],
                },
            }
        )
        return email


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--users", type=int, default=25000, help="number of users to generate")
    parser.add_argument("-s", "--seed", type=int, default=1337, help="seed for reproducible output")
    parser.add_argument("-o", "--output", default="-", help="blueprint output file, or - for stdout")
    parser.add_argument(
        "-c",
        "--credentials",
        help="k6 credentials JSON output file (default: alongside --output, or k6-users.json)",
    )
    args = parser.parse_args()

    generator = DirectoryGenerator(args.users, args.seed)
    blueprint = generator.generate()
    document = yaml.dump(
        blueprint, Dumper=BlueprintDumper, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120
    )

    credentials_path = args.credentials
    if not credentials_path:
        credentials_path = "k6-users.json" if args.output == "-" else f"{Path(args.output).with_suffix('')}.users.json"
    Path(credentials_path).write_text(json.dumps(generator.credentials, indent=2) + "\n", encoding="utf-8")

    if args.output == "-":
        sys.stdout.write(document)
    else:
        Path(args.output).write_text(document, encoding="utf-8")
        print(f"wrote {args.output}: {len(blueprint['entries'])} entries", file=sys.stderr)
    print(f"wrote {credentials_path}: {len(generator.credentials)} credentials", file=sys.stderr)


if __name__ == "__main__":
    main()
