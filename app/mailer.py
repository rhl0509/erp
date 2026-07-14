"""메일 발송(선택 기능).

SMTP_HOST 가 설정돼 있을 때만 실제로 보낸다. 미설정이면 send_mail 은 False 를 돌려주고
호출자(비밀번호 재설정)는 '관리자 임시비밀번호 발급' 경로로 안내한다.

발송 실패를 요청 실패로 바꾸지 않는다 — 실패는 로그로 남기고 False 만 돌려준다.
사용자에게는 계정 존재 여부와 발송 성패를 구분해 알려주지 않는다(계정 열거 방지).
"""
import logging
import smtplib
from email.message import EmailMessage

from .config import settings

logger = logging.getLogger("app.mailer")


def send_mail(to: str, subject: str, body: str) -> bool:
    """성공하면 True. SMTP 미설정이거나 발송 실패면 False(예외를 밖으로 던지지 않는다)."""
    if not settings.smtp_enabled or not to:
        return False

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("mail sent", extra={"to": to, "subject": subject})
        return True
    except Exception as exc:  # 발송 실패가 API 실패가 되어선 안 된다
        logger.warning("mail send failed: %s", exc, extra={"to": to, "subject": subject})
        return False
