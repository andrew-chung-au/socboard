# The deploy document (issue #237, Deploy item D20): the Systems Manager document `socboard-deploy`.
# Its content is socboard-deploy.sh as written, plus two parameters, `digest` and `environment` (dev
# or prod, default dev; issue #267), each checked by a pattern before the script runs (the script checks
# both again: a pattern is not applied when the script is run another way). The deploy role of terraform/ci-access may send this one document and nothing
# else; the apply role may change it, so a change to the script is an update of the document.
# Decision record: _docs/decisions/*-deploy-script.md. Never held: no secret, account number or ARN.
#
# VERIFY: at the first apply that Systems Manager runs a first line `#!/bin/bash` as the shebang of
# the script, and that the document is accepted at this size (the limit is 64 KB).
# Systems Manager replaces each of the two parameter references in the script (the script says where)
# and any other doubled opening brace would be read as a reference, so the script holds none.

resource "aws_ssm_document" "deploy" {
  name            = "socboard-deploy"
  document_type   = "Command"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Deploy the SOCBoard image with the given digest to the server, or leave the current release serving."
    parameters = {
      digest = {
        type           = "String"
        description    = "The image digest: sha256: and 64 lower-case hex digits."
        allowedPattern = "^sha256:[0-9a-f]{64}$"
      }
      environment = {
        type           = "String"
        description    = "The stack to deploy to: dev (the default) or prod."
        default        = "dev"
        allowedPattern = "^(dev|prod)$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      inputs = {
        timeoutSeconds = "1800"
        runCommand     = split("\n", trimsuffix(file("${path.module}/socboard-deploy.sh"), "\n"))
      }
    }]
  })

  tags = local.tags
}
