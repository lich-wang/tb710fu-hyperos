"""usage: sudo python3 vendor_diff.py MNT  (len_/port_/xm_ mounts, see mount.sh; needs the optional port_ mounts).
Classify every file of the port vendor/odm by content: identical to Lenovo stock, identical to yupei, or other.
Symlinks (to files or directories) are recorded by their target and not followed."""
import collections, hashlib, json, os, sys

M = sys.argv[1]


def _raise(e):
    raise e


def tree(root):
    if not os.path.ismount(root) or not os.listdir(root):
        sys.exit(f'{root}: not mounted or empty (run mount.sh with all three image folders)')
    out = {}
    for r, ds, fs in os.walk(root, onerror=_raise):
        for f in fs + [d for d in ds if os.path.islink(os.path.join(r, d))]:
            p = os.path.join(r, f)
            if os.path.islink(p):
                out[os.path.relpath(p, root)] = 'link:' + os.readlink(p)
                continue
            h = hashlib.sha256()
            with open(p, 'rb') as fh:
                for b in iter(lambda: fh.read(1 << 20), b''):
                    h.update(b)
            out[os.path.relpath(p, root)] = h.hexdigest()
    return out


res = {}
for part in ('vendor', 'odm'):
    L, P, X = (tree(os.path.join(M, f'{k}_{part}')) for k in ('len', 'port', 'xm'))
    lh = collections.defaultdict(list)
    for k, v in L.items():
        lh[v].append(k)
    xh = set(X.values())
    cat = collections.Counter()
    groups = collections.defaultdict(list)
    for k, v in P.items():
        if v in lh:
            c = 'same_as_lenovo' + ('' if k in L and L[k] == v else '_moved')
        elif v in xh:
            c = 'same_as_yupei'
        elif k in L:
            c = 'path_in_lenovo_differs'
        else:
            c = 'port_only'
        cat[c] += 1
        groups[c].append(k)
    only_len = sorted(k for k in L if k not in P)
    print(f'== {part}: port {len(P)} files, lenovo {len(L)}, yupei {len(X)}')
    print('  ', dict(cat))
    print(f'   lenovo files absent from port: {len(only_len)}')
    res[part] = {'groups': {k: sorted(v) for k, v in groups.items()}, 'lenovo_not_in_port': only_len}
with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'vdiff.json'), 'w') as f:
    json.dump(res, f, indent=1)
