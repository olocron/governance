-- S9 attestation tier: un-attested agents are submit-only until the founder
-- attests them (Sybil-capture + economic-DoS + weight-claim defense).
--
-- Back-compat: the founder's own rows are attested from birth (the founder
-- must be able to act immediately). All other pre-existing rows stay
-- UN-attested — they are dev/test registrations, and submit-only is the
-- correct steady-state for anyone the founder hasn't vouched for.

ALTER TABLE agent_registry
    ADD COLUMN IF NOT EXISTS attested BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE agent_registry
SET attested = TRUE
WHERE role = 'founder' OR stakeholder_type = 'founder';
