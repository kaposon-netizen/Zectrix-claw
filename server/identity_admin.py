#!/usr/bin/env python3
"""Local-only administration for experimental device credentials."""
import argparse
import getpass
import os
from pathlib import Path
from dotenv import load_dotenv
from pocketclaw.identity import IdentityStore


def main():
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser(description='管理本机家庭身份与设备凭据')
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('create-family')
    add.add_argument('name')
    add.add_argument('child_name')
    issue = sub.add_parser('issue-device')
    issue.add_argument('name')
    issue.add_argument('child_id')
    issue.add_argument('device_id')
    args = parser.parse_args()
    data_dir = Path(os.getenv('POCKETCLAW_DATA_DIR', str(root / 'data')))
    store = IdentityStore(str(data_dir / 'identities.db'))
    password = getpass.getpass('家庭密码: ')
    if args.command == 'create-family':
        if password != getpass.getpass('再次输入密码: '):
            parser.error('两次密码不一致')
        family, child = store.create_family(args.name, password, args.child_name)
        print('家庭 ID:', family, '\n孩子 ID:', child)
    else:
        session = store.login(args.name, password)
        family = store.session_family(session) if session else None
        if not family:
            parser.error('家庭账号或密码错误')
        try:
            token = store.issue_device(family, args.child_id, args.device_id)
        finally:
            store.revoke_session(session)
        print('设备令牌仅显示一次，请通过安全方式配置到设备，不要提交到仓库：')
        print(token)


if __name__ == '__main__':
    main()
