"""No plaintext secrets in project files; persistent storage only Windows Credential Manager."""
import hashlib,sys

class Vault:
    def __init__(self,namespace):
        self.service='NewsDesk:'+hashlib.sha256(str(namespace).encode()).hexdigest()[:20]
        self.memory={}
    def binding(self,kind,settings):
        return settings.api_base_url if kind=='api' else f'{settings.smtp_host}:{settings.smtp_port}:{settings.smtp_mode}:{settings.smtp_username}'
    def backend(self):
        if sys.platform!='win32': raise ValueError('本版安全记住密钥仅支持 Windows 凭据管理器；其他系统请使用本次会话')
        try:
            from keyring.backends.Windows import WinVaultKeyring
            return WinVaultKeyring()
        except Exception: raise ValueError('Windows 凭据管理器不可用；密钥未写入磁盘') from None
    def put(self,kind,value,settings):
        binding=self.binding(kind,settings); key=kind+':'+hashlib.sha256(binding.encode()).hexdigest()
        if '\r' in value or '\n' in value or len(value)>4096: raise ValueError('密钥格式无效')
        if settings.remember_secrets:
            try:self.backend().set_password(self.service,key,value)
            except Exception:raise ValueError('无法写入 Windows 凭据管理器；请改用本次会话') from None
        self.memory[kind]=(binding,value)
    def get(self,kind,settings):
        binding=self.binding(kind,settings)
        if kind in self.memory and self.memory[kind][0]==binding:return self.memory[kind][1]
        if settings.remember_secrets:
            try:return self.backend().get_password(self.service,kind+':'+hashlib.sha256(binding.encode()).hexdigest()) or ''
            except Exception:return ''
        return ''
    def status(self,s):return {'api':bool(self.get('api',s)), 'smtp':bool(self.get('smtp',s)), 'persistent':s.remember_secrets,'storage':'Windows 凭据管理器' if s.remember_secrets else '仅本次会话'}
