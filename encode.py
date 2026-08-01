import json, glob, datetime

seen = {}
for f in sorted(glob.glob('tides_*.json')):
    for p in json.load(open(f))['predictions']:
        t = datetime.datetime.strptime(p['t'], '%Y-%m-%d %H:%M')
        seen[t] = (t, round(float(p['v']) * 10), p['type'])

events = sorted(seen.values(), key=lambda e: e[0])
print('events:', len(events))

# verify strict H/L alternation
bad = sum(1 for a, b in zip(events, events[1:]) if a[2] == b[2])
print('alternation violations:', bad)

epoch = events[0][0]
print('epoch:', epoch, 'first type:', events[0][2])

# wall-clock minutes since epoch
def wmin(t):
    return (t - epoch).days * 1440 + (t - epoch).seconds // 60

deltas = [wmin(b[0]) - wmin(a[0]) for a, b in zip(events, events[1:])]
heights = [e[1] for e in events]
print('delta range:', min(deltas), max(deltas))
print('height range (tenths):', min(heights), max(heights))

B36 = '0123456789abcdefghijklmnopqrstuvwxyz'
def b36(n, w):
    s = ''
    for _ in range(w):
        s = B36[n % 36] + s
        n //= 36
    return s

# format: per event 2-char base36 delta-minutes (first event delta=0) + 2-char base36 (height_tenths + 40)
out = []
prev = 0
for e in events:
    m = wmin(e[0])
    d = m - prev
    assert 0 <= d < 1296, d
    h = e[1] + 40
    assert 0 <= h < 1296, h
    out.append(b36(d, 2) + b36(h, 2))
    prev = m

blob = ''.join(out)
print('blob chars:', len(blob))

data = {
    'epoch': epoch.strftime('%Y-%m-%dT%H:%M'),
    'firstType': events[0][2],
    'blob': blob,
}
json.dump(data, open('tidedata.json', 'w'))

# round-trip check against 5 random-ish samples
prev = 0
dec = []
typ = events[0][2]
for i in range(0, len(blob), 4):
    d = B36.index(blob[i]) * 36 + B36.index(blob[i+1])
    h = B36.index(blob[i+2]) * 36 + B36.index(blob[i+3]) - 40
    prev += d
    dec.append((prev, h, typ))
    typ = 'L' if typ == 'H' else 'H'

for idx in [0, 1, 5000, 12345, len(events) - 1]:
    orig = events[idx]
    got = dec[idx]
    assert wmin(orig[0]) == got[0] and orig[1] == got[1] and orig[2] == got[2], (idx, orig, got)
    print('ok', idx, orig[0], orig[1] / 10.0, orig[2])
print('last event:', events[-1][0])
