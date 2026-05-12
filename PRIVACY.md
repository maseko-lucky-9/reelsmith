# Privacy Policy — Reelsmith

**Effective date:** 2026-05-12
**Contact:** ltmaseko7@gmail.com

## 1. Scope

Reelsmith is a personal workflow tool operated by the author for their own use. It has no end users other than its operator and does not offer accounts. This policy describes what data Reelsmith touches when it interacts with TikTok on the operator's behalf.

## 2. Data Reelsmith Processes

Reelsmith processes the following data, all belonging to the authenticated operator:

- **TikTok OAuth tokens** (access token, refresh token) issued to the operator after they consent in the TikTok login flow. Used solely to call the TikTok Content Posting API for the operator's own account.
- **Basic profile information** retrieved via the `user.info.basic` scope: TikTok open_id, union_id, display name, and avatar URL. Used to confirm which account the tokens belong to.
- **Locally rendered video files** produced from source videos the operator provides. These are uploaded to the operator's TikTok Studio Inbox as drafts via the `video.upload` scope.
- **Upload status metadata** returned by TikTok (publish IDs, terminal status strings). Stored in a local log file on the operator's own infrastructure.

Reelsmith does not collect TikTok data about anyone other than the operator. It does not collect data from other users' videos, comments, followers, or messages.

## 3. How Data Is Used

The data above is used only to:

- Authenticate the operator with TikTok.
- Upload the operator's own rendered videos to the operator's own TikTok Studio Inbox as drafts.
- Record terminal upload status for the operator's own records.

## 4. Sharing

Reelsmith does not sell, rent, or share any data with third parties. The only third party Reelsmith communicates with is TikTok itself (via the official TikTok Developer APIs), and only on behalf of the operator who consented.

## 5. Storage and Security

- OAuth tokens are stored encrypted at rest in the operator's self-hosted n8n instance, which uses an encryption key managed by the operator.
- Rendered video files and upload logs are stored on the operator's own machine and self-hosted infrastructure.
- No data is stored on any third-party cloud service controlled by the author other than TikTok itself.

## 6. Retention

- OAuth tokens are retained until the operator revokes consent in TikTok settings or deletes the credential in n8n.
- Rendered videos and logs are retained at the operator's discretion on their own infrastructure.
- Reelsmith does not retain any data on infrastructure controlled by the author beyond what the operator chooses to keep locally.

## 7. Your Rights

Because Reelsmith has no users other than its operator, there are no third-party data-subject rights to exercise against Reelsmith. If you are the operator and want to revoke access, do so in TikTok's settings at https://www.tiktok.com/setting/security-and-privacy and delete the OAuth credential from your n8n instance.

## 8. Children

Reelsmith is not intended for and does not knowingly process data of children under the age of 13 (or the applicable age of digital consent in the operator's jurisdiction).

## 9. International Transfers

When Reelsmith uploads videos to TikTok, the data is transferred to TikTok's infrastructure under TikTok's own privacy policy (https://www.tiktok.com/legal/privacy-policy).

## 10. Changes

This policy may be updated from time to time. The "Effective date" at the top reflects the most recent change.

## 11. Contact

Questions about this policy: ltmaseko7@gmail.com
