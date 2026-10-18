import hashlib,json,pathlib,re,stat
p=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/agent.yaml');s=p.stat();b=p.read_bytes();lines=b.splitlines();keys=[]
for i,line in enumerate(lines,1):
 for m in re.finditer(rb'\"([A-Za-z_][A-Za-z0-9_-]*)\"[ \t]*:',line):keys.append({'line':i,'key':m.group(1).decode('ascii','replace')})
 m=re.match(rb'^[ \t]*([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:',line)
 if m:keys.append({'line':i,'key':m.group(1).decode('ascii','replace')})
print(json.dumps({'path':str(p),'uid':s.st_uid,'mode':oct(s.st_mode&0o7777),'size_bytes':s.st_size,'sha256':hashlib.sha256(b).hexdigest(),'field_keys_only':keys,'hub_url_occurrences':len([x for x in keys if x['key']=='hub_url']),'has_secret_values_emitted':False},sort_keys=True),flush=True)
