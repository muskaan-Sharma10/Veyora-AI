"""
Sample PDF Resume Generator
============================
Generates 5 realistic sample candidate PDF resumes for demo/testing purposes.
Uses fpdf2 library to create formatted PDFs.

Usage:
    python data/generate_samples.py

Output:
    data/sample_resumes/
        alice_chen_resume.pdf       — Strong match (all requirements)
        bob_martinez_resume.pdf     — Good match (most requirements)
        carol_okonkwo_resume.pdf    — Partial match (Python yes, cloud limited)
        david_kim_resume.pdf        — Weak match (junior level)
        emma_kowalski_resume.pdf    — Overqualified (10+ years, all requirements + more)
"""

import os
import unicodedata
from fpdf import FPDF

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "sample_resumes")


class ResumePDF(FPDF):
    """Custom FPDF subclass for structured resume layout."""

    ACCENT = (99, 102, 241)   # Purple accent
    DARK   = (20, 24, 40)
    GRAY   = (100, 110, 130)
    LIGHT  = (230, 232, 240)

    def normalize_text(self, text: str) -> str:
        replacements = str.maketrans({
            "\u2013": "-",
            "\u2014": "-",
            "\u2022": "-",
            "\u2192": "->",
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
        })
        normalized = unicodedata.normalize("NFKD", text.translate(replacements))
        latin_text = normalized.encode("latin-1", errors="replace").decode("latin-1")
        return super().normalize_text(latin_text)

    def header(self):
        pass  # Custom header per resume

    def add_name_header(self, name: str, title: str, contact: str):
        # Top accent bar
        self.set_fill_color(*self.ACCENT)
        self.rect(0, 0, 210, 32, "F")

        # Name
        self.set_xy(14, 7)
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, name, ln=True)

        # Title
        self.set_x(14)
        self.set_font("Helvetica", "", 12)
        self.set_text_color(200, 205, 240)
        self.cell(0, 7, title, ln=True)

        # Contact
        self.set_xy(0, 36)
        self.set_fill_color(240, 241, 248)
        self.rect(0, 33, 210, 12, "F")
        self.set_x(14)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*self.GRAY)
        self.cell(0, 12, contact, ln=True)

        self.ln(6)

    def section_title(self, title: str):
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*self.ACCENT)
        self.cell(0, 7, title.upper(), ln=True)
        # Underline
        self.set_draw_color(*self.ACCENT)
        self.set_line_width(0.5)
        _, y = self.get_x(), self.get_y()
        self.line(14, y, 196, y)
        self.ln(3)

    def body_text(self, text: str, indent: int = 0):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*self.DARK)
        self.set_x(14 + indent)
        self.multi_cell(0, 5, text)

    def bullet(self, text: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*self.DARK)
        self.set_x(16)
        self.cell(5, 5, "\u2022", ln=False)
        self.set_x(21)
        self.multi_cell(0, 5, text)

    def job_entry(self, title: str, company: str, period: str, bullets: list):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*self.DARK)
        self.set_x(14)
        self.cell(130, 6, title, ln=False)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*self.GRAY)
        self.cell(0, 6, period, align="R", ln=True)

        self.set_x(14)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(*self.ACCENT)
        self.cell(0, 5, company, ln=True)
        self.ln(1)

        for b in bullets:
            self.bullet(b)
        self.ln(2)

    def skills_row(self, label: str, skills: str):
        self.set_x(14)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*self.GRAY)
        self.cell(35, 5, label + ":", ln=False)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*self.DARK)
        self.multi_cell(0, 5, skills)


# ─── Resume 1: Alice Chen — Strong Match ─────────────────────────────────────

def create_alice_chen():
    pdf = ResumePDF()
    pdf.add_page()
    pdf.add_name_header(
        "Alice Chen",
        "Senior Python Developer & Cloud Architect",
        "alice.chen@email.com  |  +1 (415) 555-0101  |  linkedin.com/in/alicechen  |  github.com/alicechen",
    )

    pdf.section_title("Professional Summary")
    pdf.body_text(
        "Results-driven Senior Python Developer with 7+ years of production experience building "
        "scalable data infrastructure on AWS. Expert in FastAPI, Docker, and Kubernetes. AWS "
        "Solutions Architect Associate certified. Passionate about clean architecture, CI/CD "
        "automation, and open-source contributions."
    )

    pdf.section_title("Technical Skills")
    pdf.skills_row("Languages",   "Python (7 yrs), SQL, Bash, TypeScript")
    pdf.skills_row("Frameworks",  "FastAPI, Django REST Framework, Celery, SQLAlchemy")
    pdf.skills_row("Cloud / AWS", "EC2, S3, Lambda, RDS, ECS, CloudFormation, IAM, CloudWatch")
    pdf.skills_row("DevOps",      "Docker, Kubernetes (EKS), Terraform, GitHub Actions, Jenkins, CircleCI")
    pdf.skills_row("Databases",   "PostgreSQL, MongoDB, Redis, DynamoDB")
    pdf.skills_row("Other",       "Apache Kafka, Prometheus, Grafana, Datadog, Agile/Scrum, Git")

    pdf.section_title("Work Experience")
    pdf.job_entry(
        "Senior Python Developer", "DataFlow Systems", "Mar 2021 – Present",
        [
            "Architected a real-time event streaming platform processing 2B+ events/day using Python + Apache Kafka on AWS ECS",
            "Reduced API response time by 65% by redesigning FastAPI microservices with async/await and Redis caching",
            "Led migration of 12 legacy Django services to containerised Kubernetes clusters (EKS), reducing infra costs by 40%",
            "Built and maintained CI/CD pipelines using GitHub Actions; reduced deployment time from 45 min to under 8 min",
            "Mentored 4 junior developers through weekly code reviews and pair programming sessions",
        ],
    )
    pdf.job_entry(
        "Python Developer", "CloudBridge Inc.", "Jun 2018 – Feb 2021",
        [
            "Developed RESTful APIs using Django REST Framework serving 500K daily active users",
            "Designed PostgreSQL schema for multi-tenant SaaS platform; implemented row-level security",
            "Containerised 8 microservices using Docker and deployed to AWS ECS with auto-scaling",
            "Implemented automated test suite (pytest) achieving 92% code coverage",
        ],
    )
    pdf.job_entry(
        "Junior Python Developer", "StartTech Labs", "Jan 2017 – May 2018",
        [
            "Built internal data pipeline scripts in Python for ETL workflows",
            "Developed REST APIs with Flask for internal tooling",
        ],
    )

    pdf.section_title("Education")
    pdf.body_text("B.Sc. Computer Science — Stanford University (2016)")

    pdf.section_title("Certifications")
    pdf.bullet("AWS Solutions Architect Associate (SAA-C03) — 2022")
    pdf.bullet("AWS Developer Associate — 2023")
    pdf.bullet("Certified Kubernetes Administrator (CKA) — 2023")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, "alice_chen_resume.pdf"))
    print("Created: alice_chen_resume.pdf")


# ─── Resume 2: Bob Martinez — Good Match ─────────────────────────────────────

def create_bob_martinez():
    pdf = ResumePDF()
    pdf.add_page()
    pdf.add_name_header(
        "Bob Martinez",
        "Python Backend Developer",
        "bob.martinez@email.com  |  +1 (628) 555-0202  |  linkedin.com/in/bobmartinez",
    )

    pdf.section_title("Summary")
    pdf.body_text(
        "Backend Python developer with 5 years of experience building production APIs and data "
        "services. Comfortable with AWS infrastructure, Docker, and PostgreSQL. Currently expanding "
        "knowledge of Kubernetes and infrastructure-as-code tooling."
    )

    pdf.section_title("Skills")
    pdf.skills_row("Languages",   "Python (5 yrs), JavaScript, SQL")
    pdf.skills_row("Frameworks",  "FastAPI, Flask, Django, SQLAlchemy")
    pdf.skills_row("Cloud",       "AWS EC2, S3, RDS, Lambda; some CloudFormation experience")
    pdf.skills_row("DevOps",      "Docker, GitHub Actions, basic Kubernetes (learning)")
    pdf.skills_row("Databases",   "PostgreSQL, MySQL, Redis")
    pdf.skills_row("Other",       "Agile/Scrum, Git, pytest, Celery")

    pdf.section_title("Experience")
    pdf.job_entry(
        "Python Backend Developer", "Nexus Analytics", "Feb 2020 – Present",
        [
            "Developed and maintained 15+ RESTful API endpoints using FastAPI serving 200K daily requests",
            "Migrated on-premise application to AWS (EC2 + RDS + S3), improving reliability to 99.9% uptime",
            "Containerised services with Docker; set up GitHub Actions CI/CD pipeline",
            "Optimised slow PostgreSQL queries resulting in 50% reduction in p95 latency",
            "Participated in Agile sprints, daily stand-ups, and sprint retrospectives",
        ],
    )
    pdf.job_entry(
        "Junior Python Developer", "WebForge Studios", "Aug 2018 – Jan 2020",
        [
            "Built Django REST Framework APIs for client-facing web applications",
            "Wrote unit and integration tests (pytest), maintaining 80% code coverage",
            "Assisted in Docker setup for local development environments",
        ],
    )

    pdf.section_title("Education")
    pdf.body_text("B.Sc. Information Technology — San Jose State University (2018)")

    pdf.section_title("Certifications")
    pdf.bullet("AWS Cloud Practitioner — 2021")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, "bob_martinez_resume.pdf"))
    print("Created: bob_martinez_resume.pdf")


# ─── Resume 3: Carol Okonkwo — Partial Match ─────────────────────────────────

def create_carol_okonkwo():
    pdf = ResumePDF()
    pdf.add_page()
    pdf.add_name_header(
        "Carol Okonkwo",
        "Full-Stack Python Developer",
        "carol.okonkwo@email.com  |  +1 (510) 555-0303  |  github.com/carolokonkwo",
    )

    pdf.section_title("About Me")
    pdf.body_text(
        "Full-stack developer with 4 years of experience primarily in Python web development. "
        "Strong in Django and PostgreSQL. Limited cloud infrastructure experience — primarily "
        "frontend-adjacent backend roles. Eager to grow into platform engineering."
    )

    pdf.section_title("Technical Skills")
    pdf.skills_row("Languages",  "Python (4 yrs), JavaScript, TypeScript, HTML/CSS")
    pdf.skills_row("Frameworks", "Django, Flask, React, Next.js")
    pdf.skills_row("Databases",  "PostgreSQL, SQLite, MySQL")
    pdf.skills_row("DevOps",     "Basic Docker, Heroku deployment, GitHub")
    pdf.skills_row("Cloud",      "Limited AWS experience (S3 for file storage only)")
    pdf.skills_row("Other",      "REST APIs, Git, Agile, Jira")

    pdf.section_title("Work Experience")
    pdf.job_entry(
        "Full-Stack Developer", "BrightPath EdTech", "May 2021 – Present",
        [
            "Built Django backend APIs for e-learning platform serving 50K registered users",
            "Developed responsive frontend features using React + TypeScript",
            "Managed PostgreSQL database schemas and query optimisation",
            "Used AWS S3 for student file uploads and media storage",
            "Deployed application on Heroku with basic CI via GitHub Actions",
        ],
    )
    pdf.job_entry(
        "Python Developer (Freelance)", "Self-Employed", "Jan 2020 – Apr 2021",
        [
            "Built custom Django web applications for 8 small business clients",
            "Integrated payment gateways (Stripe) and third-party APIs",
        ],
    )

    pdf.section_title("Education")
    pdf.body_text("B.Sc. Computer Science — University of Lagos (2019)")

    pdf.section_title("Certifications")
    pdf.bullet("Django for Beginners — Udemy (2020)")
    pdf.bullet("AWS Cloud Practitioner (in progress)")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, "carol_okonkwo_resume.pdf"))
    print("Created: carol_okonkwo_resume.pdf")


# ─── Resume 4: David Kim — Weak Match (Junior) ───────────────────────────────

def create_david_kim():
    pdf = ResumePDF()
    pdf.add_page()
    pdf.add_name_header(
        "David Kim",
        "Junior Python Developer",
        "david.kim@email.com  |  +1 (213) 555-0404",
    )

    pdf.section_title("Objective")
    pdf.body_text(
        "Recent computer science graduate seeking a Python development role. "
        "Completed internships and personal projects in Python, web development, "
        "and basic data analysis. Enthusiastic learner with strong academic background."
    )

    pdf.section_title("Skills")
    pdf.skills_row("Languages",  "Python (1.5 yrs), Java, C++, SQL")
    pdf.skills_row("Frameworks", "Flask (hobby projects), basic Django tutorials")
    pdf.skills_row("Other",      "Git, basic Linux, HTML/CSS, Pandas, NumPy")
    pdf.skills_row("Cloud",      "No cloud experience")

    pdf.section_title("Experience")
    pdf.job_entry(
        "Python Intern", "LocalTech Agency", "Jun 2023 – Dec 2023",
        [
            "Assisted in writing Python scripts for data cleaning and reporting",
            "Built a simple Flask REST API for internal HR tooling",
            "Wrote unit tests using pytest for existing codebase",
        ],
    )
    pdf.job_entry(
        "Teaching Assistant (Part-time)", "UCLA CS Department", "Sep 2022 – May 2023",
        [
            "Helped 60+ students with Python programming assignments",
            "Graded coding assignments and provided feedback",
        ],
    )

    pdf.section_title("Projects")
    pdf.bullet("Personal Portfolio Site: Built with Flask, deployed on PythonAnywhere")
    pdf.bullet("Book Tracker App: Django + SQLite CRUD application")
    pdf.bullet("COVID Data Visualiser: Python + Matplotlib + Pandas analysis project")

    pdf.section_title("Education")
    pdf.body_text("B.Sc. Computer Science — University of California, Los Angeles (2023) | GPA: 3.6/4.0")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, "david_kim_resume.pdf"))
    print("Created: david_kim_resume.pdf")


# ─── Resume 5: Emma Kowalski — Overqualified ─────────────────────────────────

def create_emma_kowalski():
    pdf = ResumePDF()
    pdf.add_page()
    pdf.add_name_header(
        "Emma Kowalski",
        "Principal Engineer & Distributed Systems Architect",
        "emma.kowalski@email.com  |  +1 (206) 555-0505  |  linkedin.com/in/emmakowalski  |  github.com/emmakowalski",
    )

    pdf.section_title("Executive Summary")
    pdf.body_text(
        "Principal Engineer with 11 years of deep expertise in distributed systems, Python "
        "platform engineering, and cloud-native architecture at scale. Led engineering teams of "
        "12+ at Amazon and Stripe. AWS Solutions Architect Professional certified. Open-source "
        "maintainer of pystream (8K GitHub stars). Seeking a senior IC or staff engineering role."
    )

    pdf.section_title("Core Competencies")
    pdf.skills_row("Languages",   "Python (11 yrs, expert), Go, Rust, SQL, TypeScript")
    pdf.skills_row("Frameworks",  "FastAPI, Django, gRPC, Pydantic, SQLAlchemy, Alembic")
    pdf.skills_row("AWS (Expert)","EC2, EKS, Lambda, RDS, DynamoDB, Kinesis, CloudFormation, CDK, IAM, VPC")
    pdf.skills_row("DevOps",      "Kubernetes, Helm, Terraform, ArgoCD, GitHub Actions, Buildkite, Datadog")
    pdf.skills_row("Databases",   "PostgreSQL (DBA-level), MongoDB, Redis, Cassandra, BigQuery")
    pdf.skills_row("Architecture","Microservices, Event-Driven, CQRS, Saga pattern, Service Mesh (Istio)")
    pdf.skills_row("Other",       "Apache Kafka (Confluent), Prometheus, Grafana, OpenTelemetry, Agile/SAFe")

    pdf.section_title("Experience")
    pdf.job_entry(
        "Principal Software Engineer", "Amazon Web Services (AWS)", "Mar 2019 – Present",
        [
            "Architected AWS Kinesis Data Streams consumer library (Python SDK) used by 10,000+ customers",
            "Led a team of 12 engineers building the AWS Lambda Python runtime (3.10, 3.11, 3.12)",
            "Designed multi-region active-active failover architecture handling 50M RPS",
            "Drove adoption of Infrastructure-as-Code (CDK) across 6 teams; reduced provisioning errors by 70%",
            "Authored 3 internal engineering RFCs adopted as org-wide standards",
            "Mentored 8 junior/senior engineers; 3 promoted to Staff Engineer",
        ],
    )
    pdf.job_entry(
        "Senior Python Engineer", "Stripe", "Jan 2016 – Feb 2019",
        [
            "Built core payment processing API in Python serving $200B+ in annual transaction volume",
            "Designed idempotent distributed job system using Redis + PostgreSQL; zero data loss in 3 years",
            "Led migration of 25 services from monolith to Kubernetes (GKE)",
        ],
    )
    pdf.job_entry(
        "Python Developer", "Tableau Software", "Jun 2013 – Dec 2015",
        [
            "Developed backend ETL pipeline in Python for Tableau's cloud analytics product",
            "Built REST APIs used by 100K+ enterprise users",
        ],
    )

    pdf.section_title("Education")
    pdf.body_text("M.Sc. Computer Science (Distributed Systems) — Carnegie Mellon University (2013)")
    pdf.body_text("B.Sc. Computer Engineering — Warsaw University of Technology (2011)")

    pdf.section_title("Certifications & Recognition")
    pdf.bullet("AWS Solutions Architect Professional (SAP-C02) — 2023")
    pdf.bullet("AWS DevOps Engineer Professional — 2022")
    pdf.bullet("Certified Kubernetes Administrator (CKA) — 2021")
    pdf.bullet("Open Source: pystream — 8.2K GitHub stars, 340 contributors")
    pdf.bullet("Speaker: PyCon US 2021, 2023 — \"Building Fault-Tolerant Distributed Systems in Python\"")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, "emma_kowalski_resume.pdf"))
    print("Created: emma_kowalski_resume.pdf")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nGenerating sample PDF resumes in {OUTPUT_DIR}\n")
    create_alice_chen()
    create_bob_martinez()
    create_carol_okonkwo()
    create_david_kim()
    create_emma_kowalski()
    print(f"\nAll 5 sample resumes created in {OUTPUT_DIR}/")
    print("\nResume profiles:")
    print("  1. alice_chen_resume.pdf    - Strong match (7 yrs, all requirements)")
    print("  2. bob_martinez_resume.pdf  - Good match (5 yrs, most requirements)")
    print("  3. carol_okonkwo_resume.pdf - Partial match (4 yrs, limited cloud)")
    print("  4. david_kim_resume.pdf    - Weak match (junior, 1.5 yrs)")
    print("  5. emma_kowalski_resume.pdf - Overqualified (11 yrs, Principal Eng.)")
