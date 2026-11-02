"""Per-invocation state, copied authentication, and bounded owned-file cleanup.

Only application-owned directories are removed. Native account stores and
existing Harness conversations are never swept. This is not an OS sandbox.
"""
from __future__ import annotations

import json
import getpass
import os
import re
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / 'runs' / '.harness-tmp'
OWNER = 'galbench-harness-runtime-1'
CUSTOMIZATIONS = {'hooks','plugins','mcpServers','mcp','skills','agents','subagents','instructions','customInstructions','systemPrompt','autoMemory'}


def _json_settings(text):
    # JSONC: preserve quoted URLs/strings while removing comments and trailing commas.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\r\n]*')
    text = tokens.sub(lambda m:m[0] if m[0].startswith('"') else ' ',text)
    text = re.sub(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])',lambda m:m[0] if m[0].startswith('"') else '',text)
    return json.loads(text)


def _without_customizations(value):
    if isinstance(value,dict):return {k:_without_customizations(v) for k,v in value.items() if k not in CUSTOMIZATIONS}
    if isinstance(value,list):return [_without_customizations(v) for v in value]
    return value


def _remove_owned(path):
    base = RUNTIME_ROOT.resolve()
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)() or path.resolve().parent != base:
        return False
    try:
        marker=json.loads((path/'.owner.json').read_text(encoding='utf-8'))
        if not isinstance(marker,dict) or marker.get('owner')!=OWNER:return False
    except (OSError,ValueError):return False
    def writable(function,filename,error):
        os.chmod(filename,stat.S_IWRITE|stat.S_IREAD);function(filename)
    for delay in (0,.03,.1,.25):
        if delay:time.sleep(delay)
        try:
            shutil.rmtree(path,onerror=writable);return True
        except FileNotFoundError:return True
        except OSError:continue
    return False


def cleanup_pending():
    if not RUNTIME_ROOT.is_dir():return
    for path in list(RUNTIME_ROOT.iterdir())[:100]:
        if not path.name.startswith('gb-') or not path.is_dir():continue
        try:
            marker=json.loads((path/'.owner.json').read_text(encoding='utf-8'))
            if isinstance(marker,dict) and marker.get('owner')==OWNER and (marker.get('cleanup_pending') or _owner_exited(marker.get('pid'))):_remove_owned(path)
        except (OSError,ValueError):continue


def _owner_exited(pid):
    if type(pid) is not int or pid<=0:return False
    if pid==os.getpid():return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()==87
        try:
            code=wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value!=259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0)
    except ProcessLookupError:return True
    except PermissionError:return False
    return False


class InvocationRuntime:
    def __init__(self,config):
        self.config=config;self.provider=config.provider;self.environment={};self.cleaned=False;self.path=None
        self.policy='native_no_history'

    def __enter__(self):
        RUNTIME_ROOT.mkdir(parents=True,exist_ok=True)
        if RUNTIME_ROOT.is_symlink() or getattr(RUNTIME_ROOT,'is_junction',lambda:False)():
            raise ValueError('Harness 临时目录不能是符号链接或目录联接')
        cleanup_pending()
        self.path=RUNTIME_ROOT/('gb-'+uuid.uuid4().hex);self.path.mkdir(mode=0o700)
        self._marker(False)
        self.work=self.path/'work';self.work.mkdir()
        self.home=self.path/'home';self.home.mkdir(mode=0o700)
        self.temp=self.path/'tmp';self.temp.mkdir()
        self.environment={'TMP':str(self.temp),'TEMP':str(self.temp),'TMPDIR':str(self.temp)}
        if self.config.direct_connection:
            self.environment.update({name:'' for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','ZCODE_HTTP_PROXY')})
            self.environment.update({'NO_PROXY':'*','no_proxy':'*','ZCODE_NO_PROXY':'*'})
        try:self._prepare()
        except BaseException:
            self._cleanup();raise
        return self

    def _marker(self,pending):
        (self.path/'.owner.json').write_text(json.dumps({'owner':OWNER,'cleanup_pending':pending,'provider':self.provider,'pid':os.getpid()}),encoding='utf-8')

    def _copy(self,source,target,*,settings=False):
        source=Path(source)
        if not source.is_file():return False
        if source.stat().st_size>8*1024*1024:raise ValueError('Harness 认证或配置文件过大，请检查客户端配置')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        content=source.read_bytes()
        if settings and source.suffix in {'.json','.jsonc'}:
            try:content=json.dumps(_without_customizations(_json_settings(content.decode('utf-8-sig'))),ensure_ascii=False).encode('utf-8')
            except (ValueError,UnicodeError) as error:raise ValueError('Harness 配置不是可读取的 JSON/JSONC，请先修复客户端配置') from error
        elif settings and source.suffix in {'.yaml','.yml'}:
            lines=[];skip=False
            for line in content.decode('utf-8-sig').splitlines(keepends=True):
                match=re.match(r'^([A-Za-z_][\w-]*)\s*:',line)
                if match:skip=match[1] in CUSTOMIZATIONS
                if not skip:lines.append(line)
            content=''.join(lines).encode('utf-8')
        target.write_bytes(content);target.chmod(0o600)
        return True

    def _copy_dir(self,source,target):
        source=Path(source)
        if not source.is_dir() or source.is_symlink():return
        copied=examined=0
        queue=[(source,Path())]
        while queue:
            directory,prefix=queue.pop()
            for path in directory.iterdir():
                examined+=1
                if examined>512:raise ValueError('Harness 认证目录条目过多，请检查认证目录')
                relative=prefix/path.name
                if path.is_symlink() or getattr(path,'is_junction',lambda:False)():continue
                if len(relative.parts)>3 or any(p.lower() in {'sessions','history','logs','cache','plugins','node_modules','.git'} for p in relative.parts):continue
                if path.is_dir():queue.append((path,relative));continue
                if not path.is_file():continue
                copied+=1
                if copied>128:raise ValueError('Harness 认证文件数量过多，请检查认证目录')
                self._copy(path,target/relative)

    def _prepare(self):
        p=self.provider;original=Path.home()
        if p in {'claude_code','workbuddy','qoder','codex'}:
            # Keep native keychain identity. These transports have native no-history flags.
            if p=='claude_code':
                self.environment.update({'CLAUDE_CONFIG_DIR':str(original/'.claude'),
                    'CLAUDE_CODE_SKIP_PROMPT_HISTORY':'1','CLAUDE_CODE_DISABLE_AUTO_MEMORY':'1',
                    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','CLAUDE_CODE_TMPDIR':str(self.temp)})
            if p=='workbuddy':
                self.environment['CODEBUDDY_DISABLE_SESSION_HISTORY_CLEANUP']='1'
                self.environment['CODEBUDDY_DISABLE_TRACE_COLLECTOR']='1'
            return
        self.policy='isolated_state_and_auth_snapshot'
        self.environment.update({'HOME':str(self.home),'USERPROFILE':str(self.home),
            'XDG_CONFIG_HOME':str(self.home/'.config'),'XDG_DATA_HOME':str(self.home/'.local/share'),
            'XDG_STATE_HOME':str(self.home/'.local/state'),'XDG_CACHE_HOME':str(self.home/'.cache')})
        if p=='zcode':
            # ZCode's fallback cipher key includes os.homedir(). Preserve that
            # key explicitly while moving ALL history (including SQLite) home.
            platform='win32' if os.name=='nt' else sys.platform
            try:username=os.getlogin()
            except OSError:username=getpass.getuser()
            secret=os.environ.get('ZCODE_CREDENTIAL_SECRET') or f'zcode-credential-fallback:{platform}:{original}:{username}'
            self.environment['ZCODE_CREDENTIAL_SECRET']=secret
            base=Path(os.environ.get('ZCODE_DATA_BASE_DIR',str(original)))
            source=base if base.name=='.zcode' else base/'.zcode'
            for relative in ['cli/config.json','cli/setting.json','v2/config.json','v2/setting.json','v2/provider_config.json','v2/credentials.json','v2/telemetry-state.json']:
                self._copy(source/relative,self.home/'.zcode'/relative,settings=not relative.endswith('credentials.json'))
            custom=os.environ.get('ZCODE_PERSONAL_PROVIDER_CONFIG_FILE')
            if custom:self._copy(custom,self.home/'.zcode/v2/provider_config.json',settings=True)
            for name in ('config.json','setting.json'):
                settings=self.home/'.zcode/cli'/name
                value=_json_settings(settings.read_text(encoding='utf-8')) if settings.is_file() else {}
                if not isinstance(value,dict):raise ValueError('ZCode CLI 配置应为 JSON 对象')
                value['storage']={'dir':str(self.home/'.zcode'),'sessionDbPath':str(self.home/'.zcode/cli/db/db.sqlite')}
                settings.parent.mkdir(parents=True,exist_ok=True)
                settings.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');settings.chmod(0o600)
            self.environment.update({'ZCODE_DATA_BASE_DIR':str(self.home),'ZCODE_STORAGE_DIR':str(self.home/'.zcode'),
                'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE':str(self.home/'.zcode/v2/provider_config.json')})
        elif p=='minimax_code':
            source=Path(os.environ.get('MINIMAX_DATA_DIR') or os.environ.get('MAVIS_DATA_DIR') or original/'.minimax')
            target=self.home/'.minimax'
            for name in ['config.yaml','config.yml','config.json','preferences.json']:
                self._copy(source/name,target/name,settings=True)
            for name in ['auth','credentials']:
                self._copy_dir(source/name,target/name)
            self.environment.update({'MINIMAX_DATA_DIR':str(target),'MAVIS_DATA_DIR':str(target)})
        elif p=='kimi_code':
            for name in ['.kimi','.kimi-code']:
                source=original/name;target=self.home/name
                for file in ['config.toml','config.json','device_id']:self._copy(source/file,target/file,settings=file.endswith('.json'))
                self._copy_dir(source/'credentials',target/'credentials')
            legacy=os.environ.get('KIMI_SHARE_DIR')
            if legacy:
                source=Path(legacy);target=self.home/'.kimi'
                for file in ['config.toml','config.json','device_id']:self._copy(source/file,target/file,settings=file.endswith('.json'))
                self._copy_dir(source/'credentials',target/'credentials')
            custom=os.environ.get('KIMI_CODE_HOME') or os.environ.get('KIMI_HOME')
            if custom:
                source=Path(custom)
                for file in ['config.toml','config.json','device_id']:self._copy(source/file,self.home/'.kimi-code'/file,settings=file.endswith('.json'))
                self._copy_dir(source/'credentials',self.home/'.kimi-code/credentials')
            self.environment.update({'KIMI_HOME':str(self.home/'.kimi-code'),'KIMI_CODE_HOME':str(self.home/'.kimi-code'),'KIMI_SHARE_DIR':str(self.home/'.kimi')})
        elif p=='deepseek_harness':
            source=Path(os.environ.get('DSH_HOME',str(original/'.dsh')));target=self.home/'.dsh'
            for file in ['.credentials.yaml','settings.yaml','config.json']:
                self._copy(source/file,target/file,settings=file!='.credentials.yaml')
            # Only ACP is used. Desktop/web profiles contain unrelated plugins,
            # assets and dependency trees; files-v3 is an upload history cache.
            self._copy_dir(source/'profiles/acp',target/'profiles/acp')
            self.environment['DSH_HOME']=str(target)
        elif p=='opencode':
            config=Path(os.environ['OPENCODE_CONFIG_DIR']) if os.environ.get('OPENCODE_CONFIG_DIR') else Path(os.environ.get('XDG_CONFIG_HOME',str(original/'.config')))/'opencode'
            data=Path(os.environ.get('XDG_DATA_HOME',str(original/'.local/share')))/'opencode'
            for name in ['opencode.json','opencode.jsonc','config.json']:
                self._copy(config/name,self.home/'.config/opencode'/name,settings=True)
            custom=os.environ.get('OPENCODE_CONFIG')
            if custom:self._copy(custom,self.home/'.config/opencode/opencode.jsonc',settings=True)
            self._copy(data/'auth.json',self.home/'.local/share/opencode/auth.json')
            self.environment['OPENCODE_CONFIG_DIR']=str(self.home/'.config/opencode')

    def _cleanup(self):
        if self.path is None:return
        try:self.cleaned=_remove_owned(self.path)
        except (OSError,ValueError,RuntimeError):self.cleaned=False
        if not self.cleaned:
            try:self._marker(True)
            except OSError:pass

    def __exit__(self,kind,error,traceback):
        self._cleanup()
        if error is not None and hasattr(error,'details'):
            error.details={**(error.details or {}),'harness_runtime':self.metadata()}
        return False

    def metadata(self):
        result={'history_policy':self.policy,'cleanup':'removed' if self.cleaned else 'pending'}
        if not self.cleaned and self.path:result['pending_directory']=str(self.path.relative_to(ROOT))
        return result
