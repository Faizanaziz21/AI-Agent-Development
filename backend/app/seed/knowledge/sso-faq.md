# FAQ — Single Sign-On

## How do I enable SAML SSO?
Go to Settings > Security > Single Sign-On, upload your identity provider metadata XML and map the email attribute. Test with a non-admin account before enforcing SSO.

## Users are locked out after enabling SSO
Admins can always sign in with the break-glass local account at /login/local. Check that the user's email in the identity provider matches their AgentOS email exactly.
