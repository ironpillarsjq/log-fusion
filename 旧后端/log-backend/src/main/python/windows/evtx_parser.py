"""Parse EVTX records into normalized dictionaries."""
import json
import re
from datetime import datetime
import xml.etree.ElementTree as ET

try:
    from Evtx.Evtx import Evtx
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("请先运行: pip install -r scripts/requirements.txt") from exc


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def child(parent, name):
    return next((x for x in parent if local_name(x.tag) == name), None)


def text(node):
    return None if node is None else (node.text or "").strip() or None


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def event_data(node):
    if node is None:
        return {}
    values = []
    for item in node:
        name = item.attrib.get("Name")
        value = text(item)
        if name:
            values.append({name: value})
        else:
            values.append(value)
    result = {}
    for item in values:
        if isinstance(item, dict):
            for key, value in item.items():
                if key in result:
                    result[key] = result[key] if isinstance(result[key], list) else [result[key]]
                    result[key].append(value)
                else:
                    result[key] = value
    return result or values


def parse_xml(xml, source_file, source_year):
    root = ET.fromstring(xml)
    system = child(root, "System")
    if system is None:
        raise ValueError("记录缺少 System 节点")
    provider = child(system, "Provider")
    time_created = child(system, "TimeCreated")
    correlation = child(system, "Correlation")
    execution = child(system, "Execution")
    security = child(system, "Security")
    known = {"Provider", "EventID", "Version", "Level", "Task", "Opcode", "Keywords",
             "TimeCreated", "EventRecordID", "Correlation", "Execution", "Channel", "Computer", "Security"}
    extra = {local_name(x.tag): (dict(x.attrib) or text(x)) for x in system if local_name(x.tag) not in known}
    data = {
        "source_year": source_year, "source_file": source_file,
        "provider_name": provider.attrib.get("Name") if provider is not None else None,
        "provider_guid": provider.attrib.get("Guid") if provider is not None else None,
        "event_id": text(child(system, "EventID")), "event_version": text(child(system, "Version")),
        "level": text(child(system, "Level")), "task": text(child(system, "Task")),
        "opcode": text(child(system, "Opcode")), "keywords": text(child(system, "Keywords")),
        "time_created": parse_time(time_created.attrib.get("SystemTime") if time_created is not None else None),
        "event_record_id": text(child(system, "EventRecordID")),
        "correlation_activity_id": correlation.attrib.get("ActivityID") if correlation is not None else None,
        "correlation_related_activity_id": correlation.attrib.get("RelatedActivityID") if correlation is not None else None,
        "execution_process_id": execution.attrib.get("ProcessID") if execution is not None else None,
        "execution_thread_id": execution.attrib.get("ThreadID") if execution is not None else None,
        "channel": text(child(system, "Channel")), "computer": text(child(system, "Computer")),
        "security_user_id": security.attrib.get("UserID") if security is not None else None,
        "system_extra": json.dumps(extra, ensure_ascii=False),
        "eventdata": json.dumps(event_data(child(root, "EventData")), ensure_ascii=False),
    }
    return data


def records(path, source_year):
    with Evtx(str(path)) as evtx:
        for record in evtx.records():
            yield parse_xml(record.xml(), str(path), source_year)
