# Audit Policy
### E-Commerce Realtime Analytics Pipeline

> **Document type:** Security Policy  
> **Classification:** Internal — Confidential  
> **Effective date:** 2026-05-15  
> **Review cycle:** Annual (or after any security incident)  
> **Owner:** Platform Engineering / Security Team  
> **Approver:** CTO / CISO

---

## 1. Purpose

This policy defines the audit logging requirements for the E-Commerce Realtime Analytics Pipeline. Audit logs are the primary mechanism for:

- **Detecting** unauthorized access, privilege escalation, and data exfiltration
- **Investigating** security incidents (who, what, when, from where)
- **Demonstrating compliance** with GDPR, PCI-DSS, and SOC 2 requirements
- **Supporting** forensic analysis after a security event

---

## 2. Scope

This policy applies to all components of the pipeline:

| Component | Type | Audit method |
|---|---|---|
| Nginx reverse proxy | Infrastructure | JSON access log → Elasticsearch |
| Dashboard API | Application | Structured logging → Elasticsearch |
| Apache Kafka | Middleware | Broker audit log → Elasticsearch |
| Apache Flink | Processing | Job history + task manager logs → Elasticsearch |
| Apache Druid | Database | Router access log → Elasticsearch |
| ClickHouse | Database | Query log table + system.text_log |
| Apache Airflow | Orchestration | DAG run history (PostgreSQL) |
| MinIO | Object store | S3 audit log → Elasticsearch |
| Elasticsearch | Log store | Itself (audit trail via X-Pack) |
| Docker daemon | Infrastructure | journald → Elasticsearch |
| SSH / OS | Infrastructure | /var/log/auth.log → Elasticsearch |

---

## 3. Events That Must Be Audited

### 3.1 Authentication Events

| Event | Severity | Required fields |
|---|---|---|
| Successful login (Nginx Basic Auth, SSH, API JWT) | INFO | timestamp, user, source_ip, service |
| Failed login attempt | WARN | timestamp, user, source_ip, service, reason |
| Account locked / rate-limited | WARN | timestamp, user, source_ip, lockout_duration |
| Password / secret rotated | INFO | timestamp, secret_name, rotated_by |
| New user created | INFO | timestamp, new_user, created_by, role |
| User removed / deactivated | INFO | timestamp, removed_user, removed_by |
| JWT token issued | INFO | timestamp, user, token_expiry, scopes |
| JWT token validation failure | WARN | timestamp, source_ip, failure_reason |

### 3.2 Authorization Events

| Event | Severity | Required fields |
|---|---|---|
| Access to admin routes (/druid, /flink, /kibana) | INFO | timestamp, user, source_ip, route |
| Sudo command executed | INFO | timestamp, user, command, cwd |
| Docker exec into container | INFO | timestamp, user, container, command |
| SSH key added / removed | INFO | timestamp, user, key_fingerprint, changed_by |

### 3.3 Data Events

| Event | Severity | Required fields |
|---|---|---|
| GDPR right-to-forget triggered | INFO | timestamp, user_id, trigger_reason, ticket_id |
| Large data export (>10 000 rows) | WARN | timestamp, user, query_hash, row_count |
| Schema change (Avro, Iceberg) | INFO | timestamp, subject, old_version, new_version |
| Kafka topic created / deleted | INFO | timestamp, topic, partitions, changed_by |
| Iceberg table compaction | INFO | timestamp, table, files_rewritten, snapshot_id |

### 3.4 Infrastructure Events

| Event | Severity | Required fields |
|---|---|---|
| Container started / stopped / crashed | INFO/WARN | timestamp, container, exit_code |
| Flink job submitted / cancelled | INFO | timestamp, job_id, jar_name, submitted_by |
| Secret rotated | INFO | timestamp, secret_name, rotated_by |
| Firewall rule changed (UFW) | INFO | timestamp, rule, changed_by |
| TLS certificate renewed | INFO | timestamp, domain, expiry_date |
| Disk usage > 80% | WARN | timestamp, volume, usage_pct |
| Service restart (planned / unplanned) | INFO/WARN | timestamp, service, reason |

### 3.5 Compliance Events

| Event | Severity | Required fields |
|---|---|---|
| GDPR data deletion completed | INFO | timestamp, user_id, tables_affected, rows_deleted |
| PCI-DSS access to payment logs | INFO | timestamp, user, query, row_count |
| Security audit run | INFO | timestamp, run_by, pass_count, fail_count |
| Penetration test conducted | INFO | date, tester, scope, findings_count |
| Policy review completed | INFO | date, reviewer, next_review_date |

---

## 4. Log Format

All audit events are stored in **JSON format** in Elasticsearch under the index pattern `ecom-audit-YYYY.MM.DD`.

### Mandatory fields for every audit record

```json
{
  "@timestamp":   "2026-05-15T10:30:45.123Z",
  "event.kind":   "event",
  "event.category": ["authentication"],
  "event.type":   ["info"],
  "event.action": "user.login",
  "event.outcome": "success",
  "service.name": "nginx-proxy",
  "source.ip":    "203.0.113.42",
  "user.name":    "admin",
  "host.name":    "ecom-vm-01",
  "ecs.version":  "8.0.0",
  "pipeline":     "ecom-realtime"
}
```

### Log sources and index mapping

| Index pattern | Source | Format |
|---|---|---|
| `nginx-access-*` | Nginx JSON access log | JSON (via Filebeat) |
| `nginx-error-*` | Nginx error log | Plain text (via Filebeat) |
| `ecom-api-*` | Dashboard API structured log | JSON (via Filebeat) |
| `ecom-audit-*` | Admin actions, auth events | JSON (via Filebeat) |
| `docker-events-*` | Docker daemon events | JSON (via Filebeat journald) |
| `system-auth-*` | /var/log/auth.log | Plain text (via Filebeat) |

---

## 5. Retention Policy

| Log category | Hot (fast) | Warm (compressed) | Cold (frozen) | Delete after |
|---|---|---|---|---|
| Security / audit events | 30 days | 90 days | 9 months | **365 days** |
| Nginx access logs | 30 days | 60 days | 6 months | **365 days** |
| Application logs | 14 days | 30 days | — | **90 days** |
| Infrastructure / Docker | 14 days | 30 days | — | **90 days** |
| GDPR deletion records | Permanent | Permanent | Permanent | **Never delete** |

Retention is enforced by the **Elasticsearch ILM policy** (`ecom-audit-policy`) defined in [SECURITY.md §8.4](../SECURITY.md).

**Legal hold:** If a security incident, litigation, or regulatory investigation is active, logs in scope must be placed on legal hold (ILM deletion suspended) until the matter is resolved.

---

## 6. Log Integrity

To prevent tampering:

1. **Logs are append-only** — no process has DELETE access to Elasticsearch audit indices
2. **Index lifecycle** — the ILM `delete` phase runs only after `365 days`; no early deletion
3. **Log shipping** — Filebeat sends logs over TLS to Elasticsearch; logs cannot be dropped silently without generating an error
4. **Write-once storage** — for PCI-DSS environments, ship a copy of audit logs to S3 with Object Lock (WORM) enabled
5. **Hash chaining** — future enhancement: generate a SHA-256 chain across log batches so any deletion is detectable

---

## 7. Access Control for Audit Logs

| Role | Kibana permissions | Elasticsearch permissions |
|---|---|---|
| Security analyst | Read `ecom-audit-*`, `nginx-*` | Read-only |
| Platform engineer | Read all indices | Read-only |
| CISO / auditor | Read all indices | Read-only |
| Elasticsearch admin | Create/manage indices | Full (only for ILM/template management) |
| No one | Write/delete audit indices | Denied |

Audit log access must be reviewed **quarterly** and documented.

---

## 8. Alerting Thresholds

| Alert | Trigger | Severity | Action |
|---|---|---|---|
| Auth failure spike | >10 failures/5 min from one IP | HIGH | Auto-block IP via UFW; page on-call |
| Root SSH login | Any | CRITICAL | Page CISO + on-call immediately |
| Container OOMKilled | Any | HIGH | Page on-call |
| TLS cert expiry | <14 days remaining | HIGH | Auto-renew via certbot; alert if fails |
| Disk >85% | Any volume | MEDIUM | Alert on-call; provision storage |
| Unrecognised user in logs | JWT with unknown `sub` | HIGH | Invalidate token; investigate |
| GDPR deletion failure | DAG run fails | HIGH | Alert data protection officer |
| Security audit FAIL | Any FAIL result | HIGH | Block deploy; alert on-call |

Alerts are configured in **Kibana → Security → Detection Rules**.

---

## 9. Incident Response Integration

When a security incident is declared:

1. **Preserve** — snapshot affected Elasticsearch indices immediately:
   ```bash
   curl -X PUT "http://elasticsearch:9200/ecom-audit-$(date +%Y.%m.%d)/_settings" \
     -H "Content-Type: application/json" \
     -d '{"index.blocks.write": true}'
   ```

2. **Export** — export relevant audit records to immutable storage:
   ```bash
   elasticsearch-dump \
     --input=http://elasticsearch:9200/ecom-audit-2026.05.* \
     --output=s3://ecom-incident-2026-05-15/audit-dump.json \
     --type=data
   ```

3. **Timeline** — use Kibana Timeline to reconstruct the attack chain from audit events

4. **Notify** — GDPR requires notifying the DPA within 72 hours if personal data was exposed

---

## 10. Compliance Mapping

| Requirement | Control | Audit evidence |
|---|---|---|
| GDPR Art. 30 — Records of processing | Data inventory table (SECURITY.md §9.1) | Maintained in this policy |
| GDPR Art. 17 — Right to erasure | `gdpr_right_to_forget` Airflow DAG | GDPR deletion records in `ecom-audit-*` |
| GDPR Art. 33 — Breach notification | Incident response playbook (SECURITY.md §10) | Incident log |
| PCI-DSS 10.2 — Audit log coverage | Events audited (§3 above) | Elasticsearch audit indices |
| PCI-DSS 10.5 — Log retention | 1-year retention (§5 above) | ILM policy |
| PCI-DSS 10.6 — Log review | Weekly review by security team | Kibana scheduled report |
| SOC 2 CC7.2 — Anomaly detection | Kibana alerting rules (§8 above) | Alert history in Kibana |
| SOC 2 CC6.1 — Logical access | Auth events audited (§3.1) | `ecom-audit-*` indices |

---

## 11. Responsibilities

| Role | Responsibility |
|---|---|
| **Platform Engineering** | Implement logging infrastructure, maintain Filebeat configs |
| **Security Team** | Define alert thresholds, conduct weekly log review |
| **On-call Engineer** | Respond to alerts within SLA; preserve logs during incidents |
| **Data Protection Officer** | Oversee GDPR-related audit evidence; handle deletion requests |
| **CISO** | Approve policy changes; review audit findings quarterly |
| **All engineers** | Do not delete, alter, or disable audit logs; report anomalies |

---

## 12. Policy Review History

| Date | Version | Reviewer | Change |
|---|---|---|---|
| 2026-05-15 | 1.0 | Platform Engineering | Initial policy |

*Next scheduled review: 2027-05-15*

---

*This document is subject to change. The current version is always in the repository at `docs/AUDIT_POLICY.md`.*
