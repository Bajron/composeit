Example of a couple processes organized with `composeit`

`ping` utility is used as a test application.

Variable expansions are used to figure out proper arguments depending on the operating system.
It may not work if you do not have the `python-dotenv` patched
(it comes patched if you pip install this repo directly).

Run `composeit up` in one terminal and the following commands in the other.
Alternatively just run `composeit up -d`, and carry on.

```
composeit up -d
composeit version
composeit server_info
composeit ps
composeit top
composeit images
composeit stop ping_service4
composeit restart
composeit logs
composeit down
```

This example can be used also as a smoke test.
