# Login compatibility fix

The backend login now canonicalizes Indian phone numbers, accepts legacy `+91`/`91`/space/dash formats, and migrates legacy `password` fields to bcrypt `password_hash` after a successful login. Newly created accounts are stored in canonical 10-digit form.

If login still returns `Invalid phone or password`, the account is either in a different MongoDB database/cluster than `MONGODB_URI` + `MONGODB_DB`, or the supplied password does not match the stored credential. Check the backend startup environment and the exact `users` document without exposing the password/hash.
