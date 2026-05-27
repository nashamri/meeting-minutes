from dataclasses import dataclass, field


ATTENDANCE_OPTIONS = ["حاضر", "حضرت", "لم يحضر", "لم تحضر"]
ROLE_OPTIONS = ["رئيساً", "عضواً", "أميناً"]


@dataclass
class Member:
    name: str = ""
    role: str = "عضواً"
    attendance: str = "حاضر"
    excuse: str = "-"


@dataclass
class Article:
    title: str = ""
    body: str = ""
    decision: str = ""
    legal_refs: str = ""
    target: str = "المجلس العلمي"


@dataclass
class Meeting:
    name: str = ""
    number: str = ""
    number_num: str = ""
    date: str = ""
    time: str = ""
    academic_year: str = ""
    members: list[Member] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    approval_text: str = "يوصي المجلس برفع هذا المحضر إلى سعادة رئيس الجامعة لاعتماده."
    invitees: str = "لا يوجد"
    closing_notes: str = "الإضافات والملحوظات:"
