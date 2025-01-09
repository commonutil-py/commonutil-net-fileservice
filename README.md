# commonutil-net-fileservice

This package provides helper routines for setting up file upload-and-process service.

## Limitation

### rsync over ssh

Nested rsync target folder is not supported. File update detection will not work correctly.

### moduli for DH-KEX

Pre-generated moduli will be needed for old clients.

Use system default one or generate your own copy with the following command:

```
ssh-keygen -M generate -O bits=4096 /tmp/moduli-4096.candidate
ssh-keygen -M screen -f /tmp/moduli-4096.candidate moduli-4096.passed
```
