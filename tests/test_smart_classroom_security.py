"""Offline probe and real synthetic TLS rejection tests; no school account."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import ssl
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from classroom_app.services import integration_request_probe_service as probe
from classroom_app.services import smart_classroom_integration_service as auth


class SmartSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_precise_probe_preserves_reads_and_rejects_all_mutations(self):
        base="https://edu_api.gxufl.com/api"
        for method,path in probe.SMART_READ_OPERATIONS:
            self.assertEqual(path,probe.resolve_smart_read_probe(path,method,allowed_base_url=base))
        for path in ("/teaching/checkinCourse/save","/teaching/checkinCourse/update","/teaching/checkinCourse/page/../save",
                     "/teaching/checkinCourse/pageExtra","/teaching/checkinCourse/%70age","/user/getUserInfoAnything",
                     "https://attacker.invalid/api/teaching/checkinCourse/page"):
            with self.subTest(path=path),self.assertRaises(ValueError):probe.resolve_smart_read_probe(path,"POST",allowed_base_url=base)
        with self.assertRaises(ValueError):probe.resolve_smart_read_probe("/user/getUserInfo","POST",allowed_base_url=base)

    async def test_invalid_probe_is_rejected_before_database_or_login(self):
        with patch.object(probe,"get_db_connection",side_effect=AssertionError("DB must not be opened")), \
             patch.object(probe,"open_authenticated_smart_classroom_client",side_effect=AssertionError("must not log in")):
            with self.assertRaises(ValueError):await probe.probe_integration_request(1,{"provider":"smart_classroom","method":"POST","url":"/teaching/checkinCourse/save"})

    async def test_fixed_school_destination_and_sni_cannot_be_overridden(self):
        transport=auth.SmartClassroomTransport()
        try:
            with patch.object(httpx.AsyncHTTPTransport,"handle_async_request",new=AsyncMock(return_value=httpx.Response(200))) as send:
                request=httpx.Request("GET","https://edu_api.gxufl.com/api/user/getUserInfo",headers={"Host":"attacker.invalid"},extensions={"sni_hostname":"attacker.invalid"})
                await transport.handle_async_request(request)
                self.assertEqual("edu_api.gxufl.com",request.url.host)
                self.assertEqual("edu_api.gxufl.com",request.headers["host"])
                self.assertEqual("gxufl.com",request.extensions["sni_hostname"])
                self.assertEqual(1,send.await_count)
                for url in ("http://edu_api.gxufl.com/api","https://attacker.invalid/api","https://edu_api.gxufl.com:444/api"):
                    with self.assertRaises(ValueError):await transport.handle_async_request(httpx.Request("GET",url))
                self.assertEqual(1,send.await_count)
        finally:await transport.aclose()

    def test_password_blank_only_preserves_owned_same_account(self):
        conn=sqlite3.connect(":memory:");self.addCleanup(conn.close);conn.row_factory=sqlite3.Row
        conn.execute("CREATE TABLE teacher_smart_classroom_credentials(id INTEGER,teacher_id INTEGER,platform_code TEXT,auth_method TEXT,username TEXT,password_encrypted TEXT,enabled INTEGER,updated_at TEXT)")
        conn.execute("INSERT INTO teacher_smart_classroom_credentials VALUES(1,1,'gxufl_smart_classroom','password_token','synthetic',?,1,'v1')",(auth.encrypt_academic_secret("synthetic-only"),))
        payload=auth.prepare_smart_classroom_credential_edit(conn,1,{"username":"synthetic","password":""})
        self.assertEqual("synthetic-only",payload["password"])
        for teacher,name in ((2,"synthetic"),(1,"other-account")):
            with self.assertRaises(ValueError):auth.prepare_smart_classroom_credential_edit(conn,teacher,{"username":name,"password":""})
        row=dict(conn.execute("SELECT * FROM teacher_smart_classroom_credentials").fetchone())
        generation=auth.credential_generation(row)
        for key,value in (("username","changed"),("password_encrypted","changed"),("enabled",0)):
            self.assertNotEqual(generation,auth.credential_generation({**row,key:value}))


class RealSchoolIdentityTLSContractTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup);self.root=Path(self.folder.name)

    def certificate(self,name,*,ca=None):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
        now=datetime.now(timezone.utc)
        builder=x509.CertificateBuilder().subject_name(subject).issuer_name(ca[0].subject if ca else subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]),critical=False).add_extension(x509.BasicConstraints(ca=ca is None,path_length=None),critical=True)
        builder=builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False).add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key((ca[1] if ca else key).public_key()),critical=False).add_extension(
            x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=False,
                          key_agreement=False,key_cert_sign=ca is None,crl_sign=ca is None,encipher_only=None,decipher_only=None),critical=True)
        cert=builder.sign(ca[1] if ca else key,hashes.SHA256())
        return cert,key

    def connect(self,server,trust,*,name="gxufl.com"):
        cert,key=server
        cert_path=self.root/"server.crt";key_path=self.root/"server.key";ca_path=self.root/"ca.crt"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        ca_path.write_bytes(trust.public_bytes(serialization.Encoding.PEM))
        server_ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_ctx.load_cert_chain(cert_path,key_path)
        listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen();listener.settimeout(2)
        def serve():
            try:
                raw,_=listener.accept()
                with raw:
                    with server_ctx.wrap_socket(raw,server_side=True) as secured:secured.recv(1)
            except (ssl.SSLError,OSError):pass
            finally:listener.close()
        address=listener.getsockname();thread=threading.Thread(target=serve,daemon=True);thread.start()
        try:
            with patch.dict("os.environ",{"SMART_CLASSROOM_CA_BUNDLE":str(ca_path)}):ctx=auth.smart_classroom_tls_context()
            self.assertTrue(ctx.check_hostname);self.assertEqual(ssl.CERT_REQUIRED,ctx.verify_mode)
            with socket.create_connection(address,timeout=2) as raw:
                with ctx.wrap_socket(raw,server_hostname=name) as verified:verified.send(b"x")
        finally:thread.join(timeout=3)

    def test_valid_school_name_requires_trusted_chain(self):
        ca=self.certificate("synthetic-ca")
        self.connect(self.certificate("gxufl.com",ca=ca),ca[0])
        impostor=self.certificate("untrusted-ca")
        with self.assertRaises(ssl.SSLCertVerificationError):self.connect(self.certificate("gxufl.com",ca=impostor),ca[0])

    def test_matching_ca_does_not_allow_wrong_san_or_forged_certificate(self):
        ca=self.certificate("synthetic-ca")
        with self.assertRaises(ssl.SSLCertVerificationError):self.connect(self.certificate("attacker.invalid",ca=ca),ca[0])
        with self.assertRaises(ssl.SSLCertVerificationError):self.connect(self.certificate("gxufl.com"),ca[0])


if __name__=="__main__":unittest.main()
