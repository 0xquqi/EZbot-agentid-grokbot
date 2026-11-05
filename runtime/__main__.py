"""Read-only/local case tools; live writes require a trusted host integration."""
import argparse
import json
import sys
from .cases import CaseStore


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['case-match', 'case-record', 'delegate'])
    p.add_argument('--db', required=True)
    p.add_argument('--config')
    args = p.parse_args()
    value = json.load(sys.stdin)
    if args.command == 'case-match':
        with __import__('contextlib').closing(CaseStore(args.db)) as store:
            result = store.match(value)
    elif args.command == 'case-record':
        value['review']={**value.get('review',{}),'verified':False}
        with __import__('contextlib').closing(CaseStore(args.db)) as store:
            result = {'case_id': store.record(**value)}
    else:
        p.error('Delegation requires BotRuntime with original-agent fallback and host disclosure callbacks; CLI cannot supply these.')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
