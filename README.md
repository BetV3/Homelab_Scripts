# Homelab_Scripts
A github repo that will host my scripts that I use in my homelab
To replace the SSL certificate of a vCenter Server using the `/usr/lib/vmware-vmca/bin/certificate-manager` utility, follow these steps:

---
# Replacing vCenter 7 certificates
### Prerequisites:
1. **Certificate files**:
   - The SSL certificate (`.crt` or `.pem`).
   - The private key (`.key`).
   - The Certificate Authority (CA) chain

2. **SSH Access**:
   - SSH into the vCenter Server Appliance (VCSA) as `root`.

3. **Backup**:
   - Take a snapshot of the vCenter server or ensure you have a proper backup.

---

### Steps:

1. **Log in to the VCSA via SSH**:
   ```bash
   ssh root@<vcenter-server-ip>
   ```

2. **Launch the Certificate Manager**:
   ```bash
   /usr/lib/vmware-vmca/bin/certificate-manager
   ```

3. **Choose Option 1: Replace Machine SSL certificate with Custom Certificate**:
   - The utility will display a menu. Select:
     ```
     1. Replace Machine SSL certificate with Custom Certificate
     ```
4. **Choose Option 2: Import custom certificate(s) and key(s) to replace existing Machine SSL certificate**:
   - The utility will display a menu. Select:
     ```
     2. Import custom certificate(s) and key(s) to replace existing Machine SSL certificate
     ```
5. **Enter the required details**:
   - Follow the prompts to provide the paths to your custom certificate files:
     - **Enter the path of the Machine SSL certificate**:
       ```
       /path/to/your/ssl_certificate.crt
       ```
     - **Enter the path of the private key**:
       ```
       /path/to/your/private_key.key
       ```
     - **Enter the path of the CA certificate(s)**
       ```
       /path/to/your/ca_chain.crt
       ```

6. **Confirm Replacement**:
   - The tool will validate the certificates and keys. If valid, it will proceed to replace the Machine SSL certificate.

7. **Wait for the process to complete**:
   - Certificate Manager will stop and restart vCenter services to apply the changes. This might take several minutes.

8. **Verify the Certificate Replacement**:
   - Log in to the vSphere Client (Web UI).
   - Navigate to **Administration > Certificates > Machine SSL Certificate** and verify the new certificate details.

---

### Example of Custom Certificate Paths:
```bash
Certificate file: /etc/vmware/ssl/custom_machine_ssl.crt
```
```
---BEGIN CERTIFICATE---
MACHINE CERTIFICATE
---END CERTIICATE---
...
---BEGIN CERTIFICATE---
INTERMEDIATE(S) CERTIFICATE
---END CERTIICATE---
...
---BEGIN CERTIFICATE---
ROOT CERTIFICATE
---END CERTIICATE---
```
```bash
Private key file: /etc/vmware/ssl/custom_machine_ssl.key
CA certificate chain: /etc/vmware/ssl/custom_ca_chain.crt
```
```
---BEGIN CERTIFICATE---
INTERMEDIATE(S) CERTIFICATE
---END CERTIICATE---
---BEGIN CERTIFICATE---
ROOT CERTIFICATE
---END CERTIICATE---
```

---
