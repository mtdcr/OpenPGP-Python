import OpenPGP
import OpenPGP.Crypto
import sys

wkey = OpenPGP.Message.parse(open('key', 'rb').read())[0]

data = OpenPGP.LiteralDataPacket('This is text.', 'u', 'stuff.txt')
encrypt = OpenPGP.Crypto.Wrapper(data)
encrypted = encrypt.encrypt([wkey])

print list(encrypted)

# Now decrypt it with the same key
decryptor = OpenPGP.Crypto.Wrapper(wkey)
decrypted = decryptor.decrypt(encrypted)

print list(decrypted)
