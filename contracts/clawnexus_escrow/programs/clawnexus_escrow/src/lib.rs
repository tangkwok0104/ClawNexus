use anchor_lang::prelude::*;
use anchor_lang::system_program;

declare_id!("tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv");

// ============================================================
// ClawNexus Escrow Program — Trustless On-Chain Mission Payments
//
// Every SOL that flows through ClawNexus is protected by this
// program. No human — not even the founders — can touch funds
// that are locked in escrow. Only the code decides.
//
// Security Model:
//   ✅ Private keys never stored on servers
//   ✅ Escrow controlled by PDA (program-derived address)
//   ✅ All state transitions are verified on-chain
//   ✅ Funds auto-refund on deadline expiry
// ============================================================

/// Platform commission rate: 2% (represented as basis points)
const PLATFORM_COMMISSION_BPS: u64 = 200; // 200 basis points = 2%

/// Minimum escrow amount in lamports (0.01 SOL = 10_000_000 lamports)
const MIN_ESCROW_LAMPORTS: u64 = 10_000_000;

/// Maximum escrow amount in lamports (100 SOL)
const MAX_ESCROW_LAMPORTS: u64 = 100_000_000_000;

#[program]
pub mod clawnexus_escrow {
    use super::*;

    /// Create and fund an escrow for a mission.
    ///
    /// The client (Student/Hiring Manager) locks SOL into a PDA vault.
    /// - 2% commission is immediately sent to the platform treasury
    /// - The remaining 98% is held in escrow until mission completion
    ///
    /// Only the client can release or refund the escrow.
    pub fn create_escrow(
        ctx: Context<CreateEscrow>,
        mission_id: [u8; 32],
        amount: u64,
        deadline: i64,
    ) -> Result<()> {
        // --- Validation ---
        require!(amount >= MIN_ESCROW_LAMPORTS, EscrowError::AmountTooSmall);
        require!(amount <= MAX_ESCROW_LAMPORTS, EscrowError::AmountTooLarge);

        let clock = Clock::get()?;
        require!(
            deadline > clock.unix_timestamp,
            EscrowError::DeadlineInPast
        );

        // --- Calculate fees using checked math ---
        let commission = amount
            .checked_mul(PLATFORM_COMMISSION_BPS)
            .ok_or(EscrowError::MathOverflow)?
            .checked_div(10_000)
            .ok_or(EscrowError::MathOverflow)?;

        let net_amount = amount
            .checked_sub(commission)
            .ok_or(EscrowError::MathOverflow)?;

        // --- Transfer commission to platform treasury ---
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.client.to_account_info(),
                    to: ctx.accounts.platform_treasury.to_account_info(),
                },
            ),
            commission,
        )?;

        // --- Transfer net amount to escrow vault PDA ---
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.client.to_account_info(),
                    to: ctx.accounts.escrow_vault.to_account_info(),
                },
            ),
            net_amount,
        )?;

        // --- Initialize escrow account state ---
        let escrow = &mut ctx.accounts.escrow_account;
        escrow.mission_id = mission_id;
        escrow.client = ctx.accounts.client.key();
        escrow.mentor = ctx.accounts.mentor.key();
        escrow.gross_amount = amount;
        escrow.commission = commission;
        escrow.net_amount = net_amount;
        escrow.platform_treasury = ctx.accounts.platform_treasury.key();
        escrow.status = EscrowStatus::Funded;
        escrow.created_at = clock.unix_timestamp;
        escrow.deadline = deadline;
        escrow.bump = ctx.bumps.escrow_account;
        escrow.vault_bump = ctx.bumps.escrow_vault;

        msg!(
            "Escrow created: {} lamports locked (commission: {}, net: {})",
            amount,
            commission,
            net_amount
        );

        Ok(())
    }

    /// Release escrow — Client approves the mission, Mentor gets paid.
    ///
    /// ONLY the original client (who funded the escrow) can call this.
    /// The net amount goes from the escrow vault to the mentor's wallet.
    pub fn release_escrow(ctx: Context<ReleaseEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;

        // Only funded escrows can be released
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        let net_amount = escrow.net_amount;
        let mission_id = escrow.mission_id;
        let client_key = escrow.client;
        let vault_bump = escrow.vault_bump;

        // --- CPI Transfer from vault PDA to mentor ---
        let vault_seeds: &[&[u8]] = &[
            b"vault",
            mission_id.as_ref(),
            client_key.as_ref(),
            &[vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.mentor.to_account_info(),
                },
                &[vault_seeds],
            ),
            net_amount,
        )?;

        // --- Update state ---
        let escrow = &mut ctx.accounts.escrow_account;
        escrow.status = EscrowStatus::Completed;

        msg!(
            "Escrow released: {} lamports paid to mentor",
            net_amount
        );

        Ok(())
    }

    /// Refund escrow — Client cancels the mission, gets net amount back.
    ///
    /// Platform keeps the 2% commission (non-refundable processing fee).
    /// ONLY the original client can call this.
    pub fn refund_escrow(ctx: Context<RefundEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;

        // Only funded escrows can be refunded
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        let net_amount = escrow.net_amount;
        let mission_id = escrow.mission_id;
        let client_key = escrow.client;
        let vault_bump = escrow.vault_bump;

        // --- CPI Transfer from vault PDA back to client ---
        let vault_seeds: &[&[u8]] = &[
            b"vault",
            mission_id.as_ref(),
            client_key.as_ref(),
            &[vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.client.to_account_info(),
                },
                &[vault_seeds],
            ),
            net_amount,
        )?;

        // --- Update state ---
        let escrow = &mut ctx.accounts.escrow_account;
        escrow.status = EscrowStatus::Refunded;

        msg!(
            "Escrow refunded: {} lamports returned to client (commission retained)",
            net_amount
        );

        Ok(())
    }

    /// Expire escrow — Anyone can call this after the deadline passes.
    ///
    /// Acts as a permissionless crank: if the deadline has passed and the
    /// escrow is still in Funded status, auto-refund to the client.
    pub fn expire_escrow(ctx: Context<ExpireEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;

        // Only funded escrows can expire
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        // Check deadline
        let clock = Clock::get()?;
        require!(
            clock.unix_timestamp >= escrow.deadline,
            EscrowError::DeadlineNotReached
        );

        let net_amount = escrow.net_amount;
        let mission_id = escrow.mission_id;
        let client_key = escrow.client;
        let vault_bump = escrow.vault_bump;

        // --- CPI Auto-refund to client ---
        let vault_seeds: &[&[u8]] = &[
            b"vault",
            mission_id.as_ref(),
            client_key.as_ref(),
            &[vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.client.to_account_info(),
                },
                &[vault_seeds],
            ),
            net_amount,
        )?;

        // --- Update state ---
        let escrow = &mut ctx.accounts.escrow_account;
        escrow.status = EscrowStatus::Expired;

        msg!(
            "Escrow expired: {} lamports auto-refunded to client",
            net_amount
        );

        Ok(())
    }
}

// ============================================================
// Account Structures (On-Chain State)
// ============================================================

#[account]
#[derive(InitSpace)]
pub struct EscrowAccount {
    /// Unique mission identifier (32 bytes)
    pub mission_id: [u8; 32],
    /// Public key of the client who funded the escrow
    pub client: Pubkey,
    /// Public key of the mentor who will receive payment
    pub mentor: Pubkey,
    /// Total amount deposited (in lamports)
    pub gross_amount: u64,
    /// Platform commission (2%, in lamports)
    pub commission: u64,
    /// Amount the mentor receives (gross - commission, in lamports)
    pub net_amount: u64,
    /// Platform treasury that received the commission
    pub platform_treasury: Pubkey,
    /// Current escrow status
    pub status: EscrowStatus,
    /// When the escrow was created (unix timestamp)
    pub created_at: i64,
    /// Auto-refund deadline (unix timestamp)
    pub deadline: i64,
    /// PDA bump for the escrow account
    pub bump: u8,
    /// PDA bump for the vault account
    pub vault_bump: u8,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace)]
pub enum EscrowStatus {
    Funded,
    Completed,
    Refunded,
    Expired,
}

// ============================================================
// Instruction Account Contexts
// ============================================================

#[derive(Accounts)]
#[instruction(mission_id: [u8; 32])]
pub struct CreateEscrow<'info> {
    /// The client funding the escrow (must sign)
    #[account(mut)]
    pub client: Signer<'info>,

    /// The mentor who will receive payment (not a signer — just a reference)
    /// CHECK: We only store the public key; no data is read from this account
    pub mentor: UncheckedAccount<'info>,

    /// The escrow state account (PDA)
    #[account(
        init,
        payer = client,
        space = 8 + EscrowAccount::INIT_SPACE,
        seeds = [b"escrow", mission_id.as_ref(), client.key().as_ref()],
        bump,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault (PDA) that holds the SOL
    /// CHECK: This is a PDA used as a native SOL vault, not a data account
    #[account(
        mut,
        seeds = [b"vault", mission_id.as_ref(), client.key().as_ref()],
        bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    /// Platform treasury wallet that receives the 2% commission
    /// CHECK: We only send SOL to this address
    #[account(mut)]
    pub platform_treasury: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ReleaseEscrow<'info> {
    /// The client who originally funded the escrow (must sign to approve)
    #[account(mut)]
    pub client: Signer<'info>,

    /// The mentor receiving payment
    /// CHECK: Validated against escrow_account.mentor
    #[account(
        mut,
        constraint = mentor.key() == escrow_account.mentor @ EscrowError::WrongMentor
    )]
    pub mentor: UncheckedAccount<'info>,

    /// The escrow state account
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), client.key().as_ref()],
        bump = escrow_account.bump,
        constraint = escrow_account.client == client.key() @ EscrowError::Unauthorized,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault holding the SOL
    /// CHECK: PDA vault validated by seeds
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), client.key().as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RefundEscrow<'info> {
    /// The client requesting the refund (must be the original funder)
    #[account(mut)]
    pub client: Signer<'info>,

    /// The escrow state account
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), client.key().as_ref()],
        bump = escrow_account.bump,
        constraint = escrow_account.client == client.key() @ EscrowError::Unauthorized,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault holding the SOL
    /// CHECK: PDA vault validated by seeds
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), client.key().as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ExpireEscrow<'info> {
    /// Anyone can call expire (permissionless crank)
    pub caller: Signer<'info>,

    /// The client who originally funded (receives the refund)
    /// CHECK: Validated against escrow_account.client
    #[account(
        mut,
        constraint = client.key() == escrow_account.client @ EscrowError::Unauthorized,
    )]
    pub client: UncheckedAccount<'info>,

    /// The escrow state account
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), escrow_account.client.as_ref()],
        bump = escrow_account.bump,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault holding the SOL
    /// CHECK: PDA vault validated by seeds
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), escrow_account.client.as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

// ============================================================
// Custom Errors
// ============================================================

#[error_code]
pub enum EscrowError {
    #[msg("Escrow amount is below the minimum (0.01 SOL)")]
    AmountTooSmall,
    #[msg("Escrow amount exceeds the maximum (100 SOL)")]
    AmountTooLarge,
    #[msg("Deadline must be in the future")]
    DeadlineInPast,
    #[msg("Deadline has not been reached yet")]
    DeadlineNotReached,
    #[msg("Invalid escrow status for this operation")]
    InvalidStatus,
    #[msg("Not authorized to perform this action")]
    Unauthorized,
    #[msg("Mentor address does not match escrow")]
    WrongMentor,
    #[msg("Arithmetic overflow")]
    MathOverflow,
}
