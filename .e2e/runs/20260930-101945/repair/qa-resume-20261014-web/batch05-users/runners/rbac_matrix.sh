#!/usr/bin/env bash
# RBAC denied-write matrix for P19-02 on the isolated instance.
# Only operator and viewer issue the mutating attempts; fixtures must remain intact.
set -u
BASE=http://192.168.100.209:18081
OP=$(cat /tmp/opencode/qa-users1410-op.token)
VW=$(cat /tmp/opencode/qa-users1410-view.token)
jqf(){ python3 -c "import json;print(json.load(open('/tmp/opencode/qa1410-fixtures.json'))['$1'])"; }
TID=$(jqf task); SID=$(jqf script); AID=$(jqf application); ARTID=$(jqf artifact); SCHID=$(jqf schedule)

code(){ curl -s -o /dev/null -w "%{http_code}" "$@"; }
post(){ TOK=$1; URL=$2; BODY=$3; code -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d "$BODY" "$BASE$URL"; }
put(){  TOK=$1; URL=$2; BODY=$3; code -X PUT  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d "$BODY" "$BASE$URL"; }
del(){  TOK=$1; URL=$2; code -X DELETE -H "Authorization: Bearer $TOK" "$BASE$URL"; }
get(){  TOK=$1; URL=$2; code -H "Authorization: Bearer $TOK" "$BASE$URL"; }
postf(){ TOK=$1; code -X POST -H "Authorization: Bearer $TOK" -F "name=qa1410-rbac-attempt" -F "version=1.0.0" -F "architecture=amd64" -F "file=@/tmp/opencode/qa1410-artifact-5ece52a9.bin" "$BASE/api/artifacts"; }

echo '{'
echo '  "target": "http://192.168.100.209:18081 (isolated instance)",'
echo '  "fixtures": {'
echo "    \"task_id\": \"$TID\", \"script_id\": \"$SID\", \"application_id\": \"$AID\", \"artifact_id\": \"$ARTID\", \"schedule_id\": \"$SCHID\""
echo '  },'
echo '  "roles": {'
first=1
for ROLE in operator viewer; do
  case $ROLE in operator) T=$OP;; viewer) T=$VW;; esac
  [ $first -eq 0 ] && echo '    ,'
  first=0
  echo "    \"$ROLE\": {"
  echo "      \"role\": \"$(curl -s -H "Authorization: Bearer $T" $BASE/api/me | python3 -c 'import json,sys;print(json.load(sys.stdin)["Role"])')\","
  echo "      \"script_GET\": $(get "$T" /api/scripts),"
  echo "      \"script_POST\": $(post "$T" /api/scripts '{"name":"qa1410-rbac-script","interpreter":"shell","content":"echo x"}'),"
  echo "      \"script_PUT\": $(put "$T" /api/scripts/$SID '{"id":"'$SID'","name":"qa1410-rbac-script-mod","interpreter":"shell","content":"echo y"}'),"
  echo "      \"script_DELETE\": $(del "$T" /api/scripts/$SID),"
  echo "      \"task_GET\": $(get "$T" /api/tasks),"
  echo "      \"task_POST\": $(post "$T" /api/tasks '{"name":"qa1410-rbac-task","type":"command","command":"echo x","target":{"type":"label","label_key":"qa1410","label_value":"5ece52a9"}}'),"
  echo "      \"task_PUT\": $(put "$T" /api/tasks/$TID '{"id":"'$TID'","name":"qa1410-rbac-task-mod","type":"command","command":"echo y","target":{"type":"label","label_key":"qa1410","label_value":"5ece52a9"}}'),"
  echo "      \"task_run\": $(post "$T" /api/tasks/$TID/run '{}'),"
  echo "      \"task_DELETE\": $(del "$T" /api/tasks/$TID),"
  echo "      \"schedule_GET\": $(get "$T" /api/schedules),"
  echo "      \"schedule_POST\": $(post "$T" /api/schedules '{"task_id":"'$TID'","type":"cron","expression":"*/7 * * * *","timezone":"Asia/Shanghai","execution_owner":"agent","offline_policy":"allow_offline","misfire_policy":"run_once","enabled":true}'),"
  echo "      \"schedule_PUT\": $(put "$T" /api/schedules/$SCHID '{"id":"'$SCHID'","task_id":"'$TID'","type":"cron","expression":"*/8 * * * *","timezone":"Asia/Shanghai","execution_owner":"agent","offline_policy":"allow_offline","misfire_policy":"run_once","enabled":true}'),"
  echo "      \"schedule_DELETE\": $(del "$T" /api/schedules/$SCHID),"
  echo "      \"artifact_GET\": $(get "$T" /api/artifacts),"
  echo "      \"artifact_POST_upload\": $(postf "$T"),"
  echo "      \"artifact_DELETE\": $(del "$T" /api/artifacts/$ARTID),"
  echo "      \"application_GET\": $(get "$T" /api/applications),"
  echo "      \"application_POST\": $(post "$T" /api/applications '{"name":"qa1410-rbac-app","version":"1.0.0"}'),"
  echo "      \"application_PUT\": $(put "$T" /api/applications/$AID '{"id":"'$AID'","name":"qa1410-rbac-app-mod","version":"1.0.0"}'),"
  echo "      \"application_DELETE\": $(del "$T" /api/applications/$AID),"
  echo "      \"node_status_POST\": $(post "$T" /api/nodes/00000000-0000-0000-0000-000000000000/status '{"status":"online"}')"
  echo "    }"
done
echo '  }'
echo '}'
