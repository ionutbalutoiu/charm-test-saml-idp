# SimpleSAMLphp Juju Charm

This Juju charm will configure a local SAML-based IDP instance using the
[SimpleSAMLphp](https://simplesamlphp.org/) project.

## Deployment

The charm can be deployed with:

```
juju deploy cs:~ionutbalutoiu/test-saml-idp
```

After it is deployed, it will stay in blocked state with the message:
```
sp-metadata resource is not a well-formed xml file
```
until a valid XML SP metadata file is attached via:
```
juju attach-resource test-saml-idp sp-metadata=./sp-metadata.xml
```

For authentication, there is a fixed set of a user/password credentials defined
in the charm config via `auth-user-name` and `auth-user-password`.
